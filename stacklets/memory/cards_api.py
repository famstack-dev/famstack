"""The family memory's cards as JSON, for pages that show them.

The memory stacklet owns the cards, so it serves them: core's card browser
(and later the family dashboard) renders, this answers. The routes are the
read operations of the card graph (`graph.py`), the same ones `stack memory
find`, `card` and `links` print, so a page and the CLI never disagree:

    GET /find?q=<words>&type=&person=&since=&until=&sort=newest&limit=&audience=
    GET /card/<id>?audience=
    GET /links/<id>?depth=2&audience=
    GET /history?limit=&audience=
    GET /people
    GET /fonts/<file>          the famstack faces the wiki serves

`audience` is who will see the answer (person slugs, comma-separated): a
card in a person's own bucket only when they are the whole audience. No
audience is the admin's view.

It listens on the stack's network only and is never published: the one
way in from a browser is core's card page, which passes reads through.
The curator starts it (`serve`); standard library only, like the curator.

The graph is built from the files and the git log, so it is kept per
version of the brain and the vault and built again only when one moved.
"""

from __future__ import annotations

import json
import mimetypes
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from graph import Card, Graph, load_graph  # noqa: E402

PORT = 8090
# What a result page lists at most; a page asks for more by narrowing.
FIND_LIMIT = 60
HISTORY_LIMIT = 40
_FRONTMATTER_END = "\n---\n"


@dataclass
class CardsAPI:
    """Answers the routes above for one copy of the family memory.

        api = CardsAPI(brain=Path("/data/memory/brain"), vault=Path("/data/memory/vault"))
        status, kind, body = api.answer("/card/topic/camping", {})
    """

    brain: Path
    vault: Path | None = None
    fonts: Path | None = None
    shared_bucket: str = "family"
    _graphs: dict = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def answer(self, path: str, query: dict[str, str]) -> tuple[int, str, bytes]:
        """(status, content type, body) for a GET of `path` with `query`."""
        route, _, rest = path.strip("/").partition("/")
        if route == "fonts":
            return self._font(rest)
        audience = _audience(query.get("audience"))
        graph = self._graph(audience)
        if route == "find":
            return _json(_found(graph, query))
        if route == "card":
            card = graph.card(unquote(rest))
            return _json(_full(card, graph)) if card else _missing(rest)
        if route == "links":
            card = graph.card(unquote(rest))
            if card is None:
                return _missing(rest)
            depth = max(1, min(_int(query.get("depth"), 1), 3))
            return _json({"card": _summary(card), "around": _around(graph, card, depth)})
        if route == "history":
            return _json(self._history(graph, _int(query.get("limit"), HISTORY_LIMIT)))
        if route == "people":
            return _json({"people": [_summary(c) for c in graph.cards.values()
                                     if c.type == "person"]})
        return 404, "application/json", b'{"error": "no such route"}'

    def _graph(self, audience: frozenset[str] | None) -> Graph:
        key = (_head(self.brain), _head(self.vault), audience)
        with self._lock:
            if key not in self._graphs:
                self._graphs = {k: g for k, g in self._graphs.items() if k[:2] == key[:2]}
                self._graphs[key] = load_graph(self.brain, history=self.vault, audience=audience,
                                               shared_bucket=self.shared_bucket)
            return self._graphs[key]

    def _history(self, graph: Graph, limit: int) -> dict:
        """The latest changes, newest first as git has them, each with its cards.

        A change touching no card the audience may read is left out.
        """
        r = subprocess.run(["git", "-C", str(self.vault or self.brain), "log", f"-{limit * 3}",
                            "--name-only", "--format=\x1e%h\x1f%ad\x1f%an\x1f%s",
                            "--date=short"], capture_output=True, text=True)
        changes = []
        for block in r.stdout.split("\x1e")[1:]:
            head, _, files = block.partition("\n")
            sha, day, who, subject = (head.split("\x1f") + ["", "", "", ""])[:4]
            cards = [graph.card(f) for f in files.split()]
            cards = [_summary(c) for c in cards if c is not None]
            if cards:
                changes.append({"sha": sha, "day": day, "who": who, "subject": subject,
                                "cards": cards})
            if len(changes) == limit:
                break
        return {"changes": changes}

    def _font(self, name: str) -> tuple[int, str, bytes]:
        file = (self.fonts / Path(name).name) if self.fonts else None
        if not file or not file.is_file() or file.suffix != ".woff2":
            return 404, "application/json", b'{"error": "no such font"}'
        return 200, "font/woff2", file.read_bytes()


# ── What a card looks like as JSON ───────────────────────────────────────

def _summary(card: Card) -> dict:
    return {"id": card.id, "type": card.type, "title": card.title, "day": card.day,
            "version": card.version, "path": card.path, "persons": list(card.persons),
            "links": len(card.links_out | card.links_in)}


def _full(card: Card, graph: Graph) -> dict:
    """A card with everything a reader can show: its fields, text, history, links."""
    body = card.text.split(_FRONTMATTER_END, 1)[1] if card.text.startswith("---") \
        and _FRONTMATTER_END in card.text else card.text
    full = {**_summary(card), "meta": _plain(card.meta), "text": body.strip(),
            "compiled": card.compiled,
            "changes": [{"sha": c.sha, "day": c.day, "at": c.at, "who": c.who, "subject": c.subject}
                        for c in card.changes[:12]],
            "links_out": _linked(card.links_out, graph), "links_in": _linked(card.links_in, graph)}
    if card.type == "list":
        full["items"] = _items(card)
    return full


def _linked(ids: set[str], graph: Graph) -> list[dict]:
    cards = [graph.cards[i] for i in ids if i in graph.cards]
    return [_summary(c) for c in sorted(cards, key=lambda c: c.day or "", reverse=True)]


def _items(card: Card) -> list[dict]:
    """A list card's items, as `stack memory list show` reads them."""
    from stack.lists import read_list  # framework; on the path wherever the memory CLI runs
    try:
        family_list = read_list(card.text, path=card.path)
    except Exception:  # a list file that does not parse shows as its text
        return []
    return [{"text": i.text, "done": i.done, "section": i.section, "due": i.due,
             "done_on": i.done_on} for i in family_list.items]


def _around(graph: Graph, card: Card, depth: int) -> list[dict]:
    """The cards within `depth` links, each with the nearer card it was reached
    through (`via`), so a page can draw the path: the card itself for the first ring."""
    found = graph.neighbours(card.id, depth)
    rings: dict[int, set[str]] = {0: {card.id}}
    for distance, _, other in found:
        rings.setdefault(distance, set()).add(other.id)
    around = []
    for distance, how, other in found:
        nearer = rings.get(distance - 1, set())
        via = next((i for i in sorted(other.links_in | other.links_out) if i in nearer), card.id)
        around.append({"distance": distance, "how": how, "via": via, "card": _summary(other)})
    return around


def _found(graph: Graph, query: dict[str, str]) -> dict:
    limit = max(1, min(_int(query.get("limit"), FIND_LIMIT), 200))
    cards = graph.find(query.get("q", ""), kind=query.get("type", ""),
                       since=query.get("since", ""), until=query.get("until", ""),
                       person=query.get("person", ""), newest=query.get("sort") == "newest",
                       limit=10_000)
    types: dict[str, int] = {}
    for card in graph.cards.values():
        types[card.type] = types.get(card.type, 0) + 1
    return {"total": len(cards), "cards": [_summary(c) for c in cards[:limit]],
            "types": dict(sorted(types.items(), key=lambda t: -t[1]))}


def _plain(value):
    """Frontmatter as JSON can carry it: dates and other values as text."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _audience(value: str | None) -> frozenset[str] | None:
    if not value:
        return None
    return frozenset(p.strip().lower() for p in value.split(",") if p.strip())


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value else default
    except ValueError:
        return default


def _head(repo: Path | None) -> str:
    if repo is None:
        return ""
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                       text=True)
    return r.stdout.strip()


def _json(data: dict) -> tuple[int, str, bytes]:
    return 200, "application/json", json.dumps(data, ensure_ascii=False).encode()


def _missing(ref: str) -> tuple[int, str, bytes]:
    return 404, "application/json", json.dumps({"error": f"no card {unquote(ref)!r}"}).encode()


# ── Serving ───────────────────────────────────────────────────────────────

def serve(api: CardsAPI, *, host: str = "0.0.0.0", port: int = PORT) -> ThreadingHTTPServer:
    """Start answering on `host:port` in a background thread; returns the server."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (the stdlib's name)
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                status, kind, body = api.answer(url.path, query)
            except Exception as e:  # one bad card must not take the API down
                status, kind = 500, "application/json"
                body = json.dumps({"error": str(e)}).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind + ("; charset=utf-8" if "json" in kind else ""))
            self.send_header("Content-Length", str(len(body)))
            if kind.startswith("font/"):
                self.send_header("Cache-Control", "max-age=604800")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002 (the stdlib's name); the curator logs
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="cards-api").start()
    return server


mimetypes.add_type("font/woff2", ".woff2")
