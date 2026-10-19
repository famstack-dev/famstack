"""The family memory as a graph of cards, read straight from the files and git.

Each card (one file of the family memory) is a node: its id, its type, its
date, the commit that last changed it, and its links to other cards. The
agent asks for cards by id and follows their links, the way it would walk
a wiki, instead of reading pages it found by text alone.

**A card is a file in the vault** (ADR-016 §3). What the wiki compiles
into the brain (a person's or a topic's page, a diary month, an index) is a
view: never a card, and read only with the card it is about.

**Node ids** are the `/go` link names (`stack.links`): `diary/<entry_id>`,
`capture/<capture_id>`, `docs/<paperless_id>`, `person/<slug>`,
`topic/<slug>`, `list/<name>`. A page with none of these is `page/<path>`.
Capture ids are Matrix event ids, long and random, so they are shown by a
unique prefix, as git shows a commit.

**History is git.** Every change to the family memory is a person's commit
(ADR-015), so the vault's log says who changed a card, when, and how; the
commit that last touched a card is its version (`@a1b2c3d`), and a card
whose version moved since the agent read it has changed.

**Recent counts more.** Finding cards scores them the way the Generative
Agents retrieval does (Park et al., 2023): how well a card matches, times
how recent it is, plus how connected it is. How fast a card ages depends
on what it is: a list goes stale in days, a diary entry over years, a
person not at all.

**Who is listening.** What a person keeps in their own bucket
(`marge/...`) is theirs: it is read only where they are the only one
listening, their direct chat. The family's bucket and the top level are
read everywhere. It is the rule that files a note by who is in the room,
turned around (one person: their bucket; two or more: the family's).

**No index.** Like `stack.cards`, this reads the files each time: a family
memory is hundreds of small files, and an index would be a second copy of
the truth that can go stale. One can be added behind `load_graph` later.
"""

from __future__ import annotations

import math
import posixpath
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from stack.cards import ARCHIVE, INDEX, card_kind, card_ref
from stack.frontmatter import FrontmatterError, parse
from stack.vault import entity_page_path

# How fast a card ages, in days until its weight halves. None: never.
HALF_LIFE_DAYS: dict[str, float | None] = {
    "list": 7, "topic": 60, "note": 90, "bookmark": 90,
    "diary": 365, "document": 365, "correspondent": 365,
    "person": None, "page": 30,
}

_SHORT_ID = 10
# Found only when asked for by kind: pages to navigate by, and lists' archives.
_LEFT_OUT = {INDEX, ARCHIVE}
_LINK = re.compile(r"\]\(([^)\s#:]+\.md|/[^)\s#:]+)\)")
_WORD = re.compile(r"[^\W_]+")
_GENERATED = re.compile(r"<!--\s*(?:begin|end):\s*generated\s*-->")
_FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


@dataclass(frozen=True)
class View:
    """A page the wiki compiles about a card: read with the card, never a card itself.

    `view` says what kind of view it is (`person`, `correspondent`, as
    ADR-017 §7 names them), `path` where it lives in the brain, `text` its
    body without the frontmatter.
    """

    view: str
    title: str
    path: str
    text: str


@dataclass(frozen=True)
class Change:
    sha: str
    day: str
    who: str
    subject: str
    at: str = ""  # the author time, to the second, for ordering within a day


@dataclass
class Card:
    id: str
    type: str
    title: str
    path: str
    day: str
    text: str
    names: tuple[str, ...] = ()
    persons: tuple[str, ...] = ()
    links_out: set[str] = field(default_factory=set)
    links_in: set[str] = field(default_factory=set)
    changes: list[Change] = field(default_factory=list)
    # The card's frontmatter as written: what a reader shows per type.
    meta: dict = field(default_factory=dict)
    views: list[View] = field(default_factory=list)

    @property
    def version(self) -> str:
        return self.changes[0].sha if self.changes else ""

    def line(self) -> str:
        """One line per card, the way results are shown."""
        version = f"  @{self.version}" if self.version else ""
        return (f"{self.id}  {self.type}  {self.day or '-'}  {self.title}"
                f"{version}  links {len(self.links_out | self.links_in)}")


class Graph:
    """Every card of one copy of the family memory, linked.

        graph = load_graph(vault, views=brain)
        graph.find("zelt kaputt")      # ranked cards
        graph.card("person/homer")     # one card, or None
        graph.shared_ids               # ids two files claim: {id: [path, ...]}
    """

    def __init__(self, cards: list[Card]):
        cards, self.shared_ids = _unique(cards)
        self.cards = {c.id: c for c in cards}
        self._by_path = {c.path: c.id for c in cards}
        self._link(cards)

    def card(self, ref: str) -> Card | None:
        ref = ref.strip().lstrip("@")
        if ref in self.cards:
            return self.cards[ref]
        path = ref.removeprefix("vault/").lstrip("/")
        if found := self.at(path):
            return found
        matches = [c for i, c in self.cards.items() if i.startswith(ref) and "/" in ref]
        return matches[0] if len(matches) == 1 else None

    def at(self, path: str) -> Card | None:
        """The card of the file at `path` (vault-relative), or None."""
        cid = self._by_path.get(path)
        return self.cards[cid] if cid else None

    def find(self, query: str, *, kind: str = "", since: str = "", until: str = "",
             person: str = "", newest: bool = False, limit: int = 8,
             today: date | None = None) -> list[Card]:
        """Cards that match `query`, best first, or newest first with `newest`.

        `kind`, a stretch of days (`since`, `until`, both inclusive) and a
        `person` (slug or any of their names: the cards about them) narrow it.
        Navigation pages and list archives come only when `kind` names them;
        without words the newest come first.
        """
        today = today or date.today()
        terms = [w for w in _WORD.findall(query.lower()) if len(w) > 2]
        about = self.person_id(person) if person else ""
        if person and not about:
            return []
        newest = newest or not terms  # nothing asked: what is new
        scored = []
        for card in self.cards.values():
            if kind and card.type != kind:
                continue
            if not kind and card.type in _LEFT_OUT:
                continue
            if since and (card.day or "") < since:
                continue
            if until and (not card.day or card.day > until):
                continue
            if about and not self._about(card, about):
                continue
            match = _match(card, terms)
            if terms and match == 0:
                continue
            score = (match or 1.0) * (0.5 + 0.5 * _freshness(card, today)) \
                + 0.1 * math.log1p(len(card.links_in))
            scored.append((score, card.day or "", card))
        order = (lambda s: (s[1], s[0])) if newest else (lambda s: (s[0], s[1]))
        scored.sort(key=order, reverse=True)
        return [c for _, _, c in scored[:limit]]

    def person_id(self, said: str) -> str:
        """The card id of the person `said` names (slug or any name), or ""."""
        said = said.strip().lower()
        if f"person/{said}" in self.cards:
            return f"person/{said}"
        return self._names.get(said, "")

    def _about(self, card: Card, person: str) -> bool:
        return card.id == person or person in card.links_out or \
            any(self._names.get(p.lower()) == person for p in card.persons)

    def neighbours(self, ref: str, depth: int = 1) -> list[tuple[int, str, Card]]:
        """(distance, how, card) for every card within `depth` links of `ref`."""
        start = self.card(ref)
        if start is None:
            return []
        seen, frontier, found = {start.id}, [start.id], []
        for distance in range(1, depth + 1):
            nxt = []
            for cid in frontier:
                here = self.cards[cid]
                for how, ids in (("links to", here.links_out), ("linked from", here.links_in)):
                    for other in sorted(ids - seen):
                        seen.add(other)
                        nxt.append(other)
                        found.append((distance, how, self.cards[other]))
            frontier = nxt
        return found

    def _link(self, cards: list[Card]) -> None:
        names = {n: c.id for c in cards if c.type == "person" for n in c.names}
        self._names = names
        topics = {c.id.split("/", 1)[1] for c in cards if c.type == "topic"}
        for card in cards:
            for _, target in links_of(card):
                other = self._by_path.get(target)
                if other and other != card.id:
                    card.links_out.add(other)
            for person in card.persons:
                pid = names.get(person.lower())
                if pid and pid != card.id:
                    card.links_out.add(pid)
            parts = card.path.split("/")
            if len(parts) > 2 and parts[1] in topics and card.type != "topic":
                card.links_out.add(f"topic/{parts[1]}")
            if card.type == ARCHIVE:
                card.links_out.add(card.id.rsplit("/", 1)[0])  # list/markt/2026-10 -> list/markt
            for topic in card.meta.get("topics") or []:  # a list names the topics it serves
                if str(topic) in topics and card.type != "topic":
                    card.links_out.add(f"topic/{topic}")
        for card in cards:
            for other in card.links_out:
                if other in self.cards:
                    self.cards[other].links_in.add(card.id)


def _unique(cards: list[Card]) -> tuple[list[Card], dict[str, list[str]]]:
    """One card per id, and the ids two or more files claim.

    A thing is one card (ADR-016 §3), so two files with one id are a fault
    in the memory, which the format check reports. Until it is fixed the
    first file in path order keeps the id and every other is named by its
    path, so none of them is lost.
    """
    owner: dict[str, Card] = {}
    shared: dict[str, list[str]] = {}
    for card in sorted(cards, key=lambda c: c.path):
        if card.id in owner:
            shared.setdefault(card.id, [owner[card.id].path]).append(card.path)
            card.id = f"page/{card.path.removesuffix('.md')}"
        owner[card.id] = card
    return list(owner.values()), shared


# ── Reading the files ─────────────────────────────────────────────────────

def load_graph(vault: Path, *, views: Path | None = None,
               audience: frozenset[str] | None = None, shared_bucket: str = "family") -> Graph:
    """The graph of the cards in `vault`, each with the views `views` holds about it.

    `vault` is the memory repo: every file in it is a card, and its git log
    is the cards' history, whose commits are the family's. `views` is the
    brain, where the wiki compiles its pages; they are read only as the
    views of a card, never as cards.

    `audience` is who will see what is read (person slugs): only the cards
    and views they may all read are in the graph, so none can be found,
    opened or seen as a link. None: everything, for the admin.
    """
    vault = Path(vault)
    changes = git_changes(vault)
    cards = []
    for path in sorted(vault.rglob("*.md")):
        rel = path.relative_to(vault).as_posix()
        if rel.startswith(".git/") or "/.git/" in rel:
            continue
        if audience is not None and not readable(rel, audience, shared_bucket=shared_bucket):
            continue
        card = _card(rel, path.read_text(encoding="utf-8", errors="replace"))
        card.changes = changes.get(rel) or []
        if not card.day and card.changes:
            card.day = card.changes[0].day
        if views is not None:
            card.views = [v for v in _views(card, Path(views))
                          if audience is None or readable(v.path, audience, shared_bucket=shared_bucket)]
        cards.append(card)
    return Graph(cards)


def _views(card: Card, brain: Path) -> list[View]:
    """The views the wiki compiled about `card`, found where it compiles them.

    Until a view names its card (`about`, ADR-016 step 3), the path is the
    only thing that ties the two: a person's view is `<slug>/about.md`, a
    correspondent's shares the card's path.
    """
    if card.type == "person":
        wanted = [("person", entity_page_path(card.id.split("/", 1)[1], "person"))]
    elif card.type == "correspondent":
        wanted = [("correspondent", card.path)]
    else:
        return []
    found = []
    for kind, rel in wanted:
        file = brain / rel
        if not file.is_file():
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        try:
            meta = parse(text) if text.startswith("---") else {}
        except FrontmatterError:
            meta = {}
        body = _GENERATED.sub("", _FRONTMATTER.sub("", text, count=1)).strip()
        found.append(View(view=kind, title=str(meta.get("title") or card.title), path=rel, text=body))
    return found


def readable(path: str, audience: frozenset[str], *, shared_bucket: str = "family") -> bool:
    """Whether everyone in `audience` may read the card at `path`.

    A person's bucket is the first folder of the path, named by their slug;
    it is read only when that person is the whole audience.
    """
    bucket, _, rest = path.partition("/")
    if not rest or bucket == shared_bucket:
        return True
    return audience == frozenset({bucket})


def git_changes(repo: Path) -> dict[str, list[Change]]:
    """Path -> its changes, newest first, from one pass over the whole log.

    The whole log, because a card's history is its oldest commits as much
    as its newest: cut at the last N commits, every card filed before them
    loses who wrote it. 10,000 commits read in about 0.4 s.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", "--name-only",
             "--format=\x1e%h\x1f%ad\x1f%an\x1f%s\x1f%aI", "--date=short"],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    changes: dict[str, list[Change]] = {}
    for block in out.split("\x1e")[1:]:
        head, _, files = block.partition("\n")
        sha, day, who, subject, at = (head.split("\x1f") + ["", "", "", "", ""])[:5]
        for name in files.split():
            changes.setdefault(name, []).append(Change(sha, day, who, subject, at))
    return changes


def _card(rel: str, text: str) -> Card:
    try:
        meta = parse(text) if text.startswith("---") else {}
    except FrontmatterError:
        meta = {}
    kind = card_kind(rel, meta)
    ref_kind, ref = card_ref(rel, meta)
    # Capture ids are Matrix event ids: shown by a unique prefix, as git shows a commit.
    card_id = f"{ref_kind}/{ref.lstrip('$')[:_SHORT_ID] if ref_kind in ('capture', 'diary') else ref}"
    title = str(meta.get("title") or Path(rel).stem).strip()
    day = str(meta.get("date") or "")[:10] or str(meta.get("timestamp") or "")[:10]
    names = tuple(n.lower() for n in [
        meta.get("slug"), meta.get("canonical"), meta.get("title"),
        *(meta.get("synonyms") or []), *(meta.get("aliases") or []),
        str(meta.get("title") or "").split(" ")[0],
    ] if n) if kind == "person" else ()
    persons = tuple(str(p) for p in (meta.get("persons") or []) if p)
    return Card(id=card_id, type=kind, title=title, path=rel, day=day,
                text=_GENERATED.sub("", text), names=names, persons=persons, meta=dict(meta))


def links_of(card: Card) -> list[tuple[str, str]]:
    """(the link as written, the vault path it points at) for each link in
    `card`'s text to another card. Links to media and to the web are not."""
    found = []
    for link in _LINK.findall(card.text):
        if target := _page(link, card.path):
            found.append((link, target))
    return found


def _page(link: str, here: str) -> str:
    """The card path a link points to: site paths (`/family/lists/dates.md`)
    and paths relative to the linking card (`../../people/lisa.md`). ""
    for a file that is no card (`/media/2026/10/photo.png`)."""
    if link.startswith("/"):
        path = link.lstrip("/")
    else:
        path = posixpath.normpath(posixpath.join(posixpath.dirname(here), link))
    if posixpath.splitext(path)[1] not in ("", ".md"):
        return ""
    return path if path.endswith(".md") else f"{path}.md"


def _match(card: Card, terms: list[str]) -> float:
    """How well a card matches: title words count most, then the text.

    A term matches inside a longer word, so "zelt" finds "Zeltplatz": German
    puts its words together.
    """
    if not terms:
        return 0.0
    title, text = card.title.lower(), card.text.lower()
    score = 0.0
    for term in terms:
        if term in title:
            score += 3.0
        elif term in text:
            score += 1.0 + min(text.count(term), 5) * 0.1
    return score / len(terms)


def _freshness(card: Card, today: date) -> float:
    half_life = HALF_LIFE_DAYS.get(card.type, HALF_LIFE_DAYS["page"])
    if half_life is None or not card.day:
        return 1.0
    try:
        age = (today - datetime.strptime(card.day, "%Y-%m-%d").date()).days
    except ValueError:
        return 1.0
    return 0.5 ** (max(age, 0) / half_life)
