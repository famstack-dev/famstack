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
    GET /report                where the memory does not meet its format yet
    GET /media/<path>?audience= a photo or recording a card embeds
    GET /fonts/<file>          the famstack faces the wiki serves
    POST /push                 Forgejo's notice of a push to the memory repo
    GET|PATCH|PUT /cards/<id>  one card to change, and the change (`card_edit.py`)

The cards are the files of the vault. A card comes as its type's renderer
writes it (`rendered`, markdown: the same the chat and `stack memory card`
show, ADR-016 §4), and with the views the wiki compiled from it into the brain (those whose `about` or `derived_from` names
it), which are never cards of their own. A view compiled from an older
version of the card is marked `behind`: the card's own text counts.

`POST /push` is the one route that is no read: the memory repo's webhook
calls it on every push, and the curator pulls at once instead of at its
next poll. It carries no secret because all it can do is make the curator
look sooner.

`/cards/<id>` is the card write endpoint, the one way to change a card;
`serve` takes it as `editor`, since it needs `jsonschema` and a Forgejo
token, which only the curator's container has.

`audience` is who will see the answer (person slugs, comma-separated): a
card in a person's own bucket only when they are the whole audience. No
audience is the admin's view. The report is the admin's and takes none
(`format_check.py`, the same as `stack memory check-format`).

A file of the media archive is served only to an audience that may read a
card embedding or linking it, so "view as" cannot open another person's
photo; the admin reads every file. Nothing outside the archive is served.

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
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from format_check import format_report  # noqa: E402
from note_card import SourcesMismatch, card_sources  # noqa: E402
from graph import Card, Graph, View, load_graph, tags_of  # noqa: E402
from stack.links import go_page  # noqa: E402
from stack.tags import tag_label  # noqa: E402

PORT = 8090
# What a result page lists at most; a page asks for more by narrowing.
FIND_LIMIT = 60
HISTORY_LIMIT = 40
_FRONTMATTER_END = "\n---\n"
# What a browser may show in the page from the media archive. Any other
# file is sent as bytes to download, so nothing in the archive runs as a
# page (an SVG or HTML a family member filed).
_MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".heic": "image/heic",
    ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".pdf": "application/pdf",
}
_NOT_FOUND = (404, "application/json", b'{"error": "no such file"}')
_CARDS = "/cards/"


class Editor(Protocol):
    """What answers `/cards/<id>`: `card_edit.CardEditor`."""

    def answer(self, method: str, card_id: str, headers: dict[str, str],
               body: bytes) -> tuple[int, dict[str, str], bytes]: ...


@dataclass
class CardsAPI:
    """Answers the routes above for one copy of the family memory.

        api = CardsAPI(vault=Path("/data/memory/vault"), brain=Path("/data/memory/brain"))
        status, kind, body = api.answer("/card/person/homer", {})
    """

    vault: Path
    brain: Path | None = None
    fonts: Path | None = None
    media: Path | None = None
    shared_bucket: str = "family"
    # The instance's, for the one label a rendered card shows.
    language: str = "en"
    _graphs: dict = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def answer(self, path: str, query: dict[str, str]) -> tuple[int, str, bytes]:
        """(status, content type, body) for a GET of `path` with `query`."""
        route, _, rest = path.strip("/").partition("/")
        if route == "fonts":
            return self._font(rest)
        if route == "report":
            return _json(format_report(self._graph(None)))
        audience = _audience(query.get("audience"))
        graph = self._graph(audience)
        if route == "media":
            return self._media(unquote(rest), graph, audience)
        if route == "find":
            return _json(_found(graph, query))
        if route == "card":
            card = graph.card(unquote(rest))
            return _json(_full(card, graph, self.language)) if card else _missing(rest)
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
        key = (_head(self.vault), _head(self.brain), audience)
        with self._lock:
            if key not in self._graphs:
                self._graphs = {k: g for k, g in self._graphs.items() if k[:2] == key[:2]}
                self._graphs[key] = load_graph(self.vault, views=self.brain, audience=audience,
                                               shared_bucket=self.shared_bucket)
            return self._graphs[key]

    def _history(self, graph: Graph, limit: int) -> dict:
        """The latest changes, newest first as git has them, each with its cards.

        A change touching no card the audience may read is left out.
        """
        r = subprocess.run(["git", "-C", str(self.vault), "log", f"-{limit * 3}",
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

    def _media(self, rel: str, graph: Graph, audience: frozenset[str] | None) -> tuple[int, str, bytes]:
        """A file of the media archive, for an audience a card refers to it for.

        A file no card of theirs refers to is answered as missing, so an
        audience cannot learn which photos another person keeps.
        """
        parts = rel.split("/")
        if not self.media or not rel or "" in parts or ".." in parts:
            return _NOT_FOUND
        if audience is not None and rel not in graph.media:
            return _NOT_FOUND
        root = self.media.resolve()
        file = (root / rel).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            return _NOT_FOUND
        kind = _MEDIA_TYPES.get(file.suffix.lower(), "application/octet-stream")
        return 200, kind, file.read_bytes()

    def _font(self, name: str) -> tuple[int, str, bytes]:
        file = (self.fonts / Path(name).name) if self.fonts else None
        if not file or not file.is_file() or file.suffix != ".woff2":
            return 404, "application/json", b'{"error": "no such font"}'
        return 200, "font/woff2", file.read_bytes()


# ── What a card looks like as JSON ───────────────────────────────────────

def _summary(card: Card) -> dict:
    return {"id": card.id, "name": card.name, "type": card.type, "title": card.title,
            "day": card.day,
            "version": card.version, "path": card.path, "persons": list(card.persons),
            "links": len(card.links_out | card.links_in)}


def _full(card: Card, graph: Graph, language: str) -> dict:
    """A card with everything a reader can show: its fields, its text and
    the markdown its type's renderer writes from them, its history, links,
    and the views compiled about it."""
    body = card.text.split(_FRONTMATTER_END, 1)[1] if card.text.startswith("---") \
        and _FRONTMATTER_END in card.text else card.text
    full = {**_summary(card), "meta": _plain(card.meta), "text": body.strip(),
            "rendered": graph.rendered(card, language=language).markdown,
            # A topic tag is its id; a reader sees its label (ADR-017 §4).
            "tag_labels": {t: tag_label(t, graph.ontology, language) for t in tags_of(card)},
            "changes": [{"sha": c.sha, "day": c.day, "at": c.at, "who": c.who, "subject": c.subject}
                        for c in card.changes[:12]],
            **_walk(card, graph), "views": [_view(v) for v in card.views]}
    if card.type == "list":
        full["items"] = _items(card)
    if card.sidecar is not None:
        full.update(_provenance(card))
    return full


def _provenance(card: Card) -> dict:
    """Where a card's sources came from and how it was made, from its sidecar
    (ADR-018 §3): each source with its quote, the filer, and `made`.

    The same list a refile reads (`note_card.card_sources`), so a reader
    shows what the model was given. A card whose quotes and sources do
    not pair up shows none; the format report names it.
    """
    try:
        sources = [s.shown() for s in card_sources(card.text, card.sidecar or {})]
    except SourcesMismatch:
        sources = []
    side = card.sidecar or {}
    return {"sources": sources, "filed_by": str(side.get("filed_by") or ""),
            "made": dict(side.get("made") or {})}


def _view(view: View) -> dict:
    """A view as a page shows it: what it is, its text, whether it is behind
    the card, and its `/go` link into the wiki where it has one
    (`/go/person/homer`)."""
    go = go_page(view.path)
    return {"view": view.view, "title": view.title, "path": view.path, "text": view.text,
            "behind": view.behind, "go": f"/go{go}" if go else ""}


def _walk(card: Card, graph: Graph) -> dict:
    """The cards on either side of `card`, as a page walks them: what links
    to it (`linked_from`) and what it links to (`links_to`).

    A link both ways is shown once, with what the card links to, and marked
    `both`, so a person and their note are not listed twice (review G11).
    """
    both = card.links_out & card.links_in
    return {"linked_from": _groups(card.links_in - both, graph, both=set()),
            "links_to": _groups(card.links_out, graph, both=both)}


def _groups(ids: set[str], graph: Graph, *, both: set[str]) -> list[dict]:
    """`ids` by card type, the largest group first, newest first inside one:
    `[{"type": "note", "count": 2, "cards": [...]}]`."""
    by_type: dict[str, list[Card]] = {}
    for cid in ids:
        if other := graph.cards.get(cid):
            by_type.setdefault(other.type, []).append(other)
    groups = []
    for kind, cards in sorted(by_type.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        cards.sort(key=lambda c: (c.day or "", c.title), reverse=True)
        groups.append({"type": kind, "count": len(cards),
                       "cards": [{**_summary(c), "both": c.id in both} for c in cards]})
    return groups


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

def serve(api: CardsAPI, *, host: str = "0.0.0.0", port: int = PORT,
          on_push: Callable[[], None] | None = None,
          editor: Editor | None = None) -> ThreadingHTTPServer:
    """Start answering on `host:port` in a background thread; returns the server.

    `on_push` is called for each `POST /push`, and `editor` answers
    `/cards/<id>` (`card_edit.CardEditor`); without one, its route is not there.
    """

    class Handler(BaseHTTPRequestHandler):
        def _card(self, method: str) -> bool:
            """Answer a request for `/cards/<id>`; False for any other path."""
            path = urlparse(self.path).path
            if editor is None or not path.startswith(_CARDS):
                return False
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            headers = {k.lower(): v for k, v in self.headers.items()}
            try:
                status, sent, answer = editor.answer(method, unquote(path[len(_CARDS):]), headers, body)
            except Exception as e:  # one bad card must not take the API down
                status, sent = 500, {"Content-Type": "application/json"}
                answer = json.dumps({"error": str(e)}).encode()
            self.send_response(status)
            for name, value in sent.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)
            return True

        def do_PATCH(self):  # noqa: N802 (the stdlib's name)
            if not self._card("PATCH"):
                self._not_found()

        def do_PUT(self):  # noqa: N802 (the stdlib's name)
            if not self._card("PUT"):
                self._not_found()

        def _not_found(self):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):  # noqa: N802 (the stdlib's name)
            # Forgejo sends the push as JSON; its content is not needed,
            # the curator reads what changed from git.
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            found = urlparse(self.path).path == "/push" and on_push is not None
            if found and on_push is not None:
                on_push()
            self.send_response(204 if found else 404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):  # noqa: N802 (the stdlib's name)
            if self._card("GET"):
                return
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
            self.send_header("X-Content-Type-Options", "nosniff")
            if kind == "application/octet-stream":
                self.send_header("Content-Disposition", "attachment")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002 (the stdlib's name); the curator logs
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="cards-api").start()
    return server


mimetypes.add_type("font/woff2", ".woff2")
