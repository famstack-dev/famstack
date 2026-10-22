"""The family memory as a graph of cards, read straight from the files and git.

Each card (one file of the family memory) is a node: its id, its type, its
date, the commit that last changed it, and its links to other cards. The
agent asks for cards by id and follows their links, the way it would walk
a wiki, instead of reading pages it found by text alone.

**A card is a file in the vault** (ADR-016 §3). What the wiki compiles
into the brain (a person's or a topic's page, a diary month, an index) is a
view: never a card, and read only with the cards it names, the one it is
about (`about`) and those it was compiled from (`derived_from`). A view
compiled from an older version of a card than the vault holds is behind
that card, and the card's own text counts.

**Node ids** are the `/go` link names (`stack.links`): a card's own
`card_id` (`note-7f3a9c21e4`, `person-homer`, ADR-016) when it has one,
and every link names a card by its node id. A card without one yet is
named by the rule from before card ids (`stack.cards.card_name`):
`diary/<entry_id>`, `capture/<capture_id>`, `docs/<paperless_id>`,
`person/<slug>`, `topic/<slug>`, `list/<name>`, and `page/<path>` for a
page with none of these. A card that has an id since keeps that name as
an alias: it opens the card as well as its id does, and so does an id the
card had before (`former_card_ids`) that no card carries now. Capture ids
are Matrix event ids, long and random, so they are shown by a unique
prefix, as git shows a commit.

**A topic is a folder of one bucket**, and so is a list's file: the
family's camping folder and Homer's are two topics (`topic-camping`,
`topic-camping-2`), though both were `topic/camping` before card ids. A
card belongs to the topic of the folder it is filed in and to the topic a
tag of its names by id, and a list's archive to the list beside it, each
in the card's own bucket.

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

import json
import math
import posixpath
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from stack.cards import (
    ABOUT,
    ARCHIVE,
    CARD_ID,
    INDEX,
    VIEW,
    card_id_from,
    card_kind,
    card_name,
    card_ref,
    card_version,
    compiled_versions,
    former_card_ids,
    is_navigation_page,
    view_names,
)
from stack.cards.lookup import SIDECAR_SUFFIX
from stack.card_render import Rendered, render, split_body
from stack.frontmatter import FrontmatterError, parse
from stack.lists import list_path
from stack.ontology import Ontology
from stack.tags import tag_words

from lib import TOPIC_CARDS, get_ontology

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
# A media embed (`![[/media/2026/10/<event id>.png]]`, ADR-017 §3), and a
# link to a file of the media archive (the record's `**File**` line).
_EMBED = re.compile(r"!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
_FILE_LINK = re.compile(r"\]\(([^)\s#]+)\)")
_MEDIA = "media/"
_WORD = re.compile(r"[^\W_]+")
_GENERATED = re.compile(r"<!--\s*(?:begin|end):\s*generated\s*-->")
# The model's last answer a view keeps for a compile without the model.
_ANSWER = re.compile(r"^<!-- answer: [A-Za-z0-9+/=]* -->\n?", re.M)
_FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


@dataclass(frozen=True)
class View:
    """A page the wiki compiles about a card: read with the card, never a card itself.

    `view` says what kind of view it is (`person`, `correspondent`, as
    ADR-017 §7 names them), `path` where it lives in the brain, `text` its
    body without the frontmatter. `behind` says the view was compiled from
    an older version of the card it is read with than the vault holds.
    """

    view: str
    title: str
    path: str
    text: str
    behind: bool = False


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
    # `kind/value` by the rule before card ids: an alias that still opens the card.
    name: str = ""
    names: tuple[str, ...] = ()
    persons: tuple[str, ...] = ()
    links_out: set[str] = field(default_factory=set)
    links_in: set[str] = field(default_factory=set)
    changes: list[Change] = field(default_factory=list)
    # The card's frontmatter as written: what a reader shows per type.
    meta: dict = field(default_factory=dict)
    views: list[View] = field(default_factory=list)
    # The git blob id of the file as it is: the version a view records of
    # each card it was compiled from (`stack.cards.card_version`).
    blob: str = ""
    # What a search finds the card's tags by besides the tags themselves: a
    # topic's id and its label in every language (`stack.tags.tag_words`).
    tag_labels: tuple[str, ...] = ()
    # The sidecar beside the card (ADR-018): where its sources came from and
    # how it was made. None for a card that has none.
    sidecar: dict | None = None

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
        graph.card("person-homer")     # one card by its id or its name, or None
        graph.shared_ids               # ids two files claim: {id: [path, ...]}
        graph.ontology                 # the topics its cards' tags name
        graph.lone_sidecars            # sidecars whose card is gone: [path, ...]
    """

    def __init__(self, cards: list[Card], ontology: Ontology | None = None,
                 lone_sidecars: list[str] | None = None):
        self.lone_sidecars = list(lone_sidecars or [])
        cards, self.shared_ids = _unique(cards)
        self.ontology = ontology or Ontology()
        self.cards = {c.id: c for c in cards}
        self._by_path = {c.path: c.id for c in cards}
        # The name each card had before card ids, to its id: what an older
        # reference opens.
        self._named = _aliases(cards)
        # The media archive's files the cards refer to (`2026/10/x.png`): what
        # this graph's audience may open.
        self.media = {m for c in cards for m in media_of(c)}
        self._link(cards)

    def card(self, ref: str) -> Card | None:
        ref = ref.strip().lstrip("@")
        if ref in self.cards:
            return self.cards[ref]
        if ref in self._named:
            return self.cards[self._named[ref]]
        path = ref.removeprefix("vault/").lstrip("/")
        if found := self.at(path):
            return found
        matches = {i for n, i in self._named.items() if n.startswith(ref) and "/" in ref}
        return self.cards[matches.pop()] if len(matches) == 1 else None

    def at(self, path: str) -> Card | None:
        """The card of the file at `path` (vault-relative), or None."""
        cid = self._by_path.get(path)
        return self.cards[cid] if cid else None

    def rendered(self, card: Card, *, language: str = "en") -> Rendered:
        """`card` as its type's renderer shows it, its people by first name."""
        return render(card.meta, split_body(card.text), names=self.person_names(),
                      language=language)

    def person_names(self) -> dict[str, str]:
        """Each declared person's id with the first name a reader knows them by.

        `{"homer": "Homer"}`: what a rendered card shows for `persons: [homer]`.
        """
        return {c.name.removeprefix("person/"): c.title.split()[0]
                for c in self.cards.values()
                if c.type == "person" and c.name.startswith("person/") and c.title.strip()}

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
        """The card id of the person `said` names (card id, slug or any name), or ""."""
        said = said.strip().lower()
        for cid in (said, self._named.get(f"person/{said}", "")):
            if (card := self.cards.get(cid)) is not None and card.type == "person":
                return card.id
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
        """Each card's links both ways, every one by the id of the card it reaches.

        A card links what its text links to, the people its `persons`
        names, and the topics it belongs to (`_topics_of`); a list's
        archive links its list.
        """
        names = {n: c.id for c in cards if c.type == "person" for n in c.names}
        self._names = names
        for card in cards:
            for _, target in links_of(card):
                other = self._by_path.get(target)
                if other and other != card.id:
                    card.links_out.add(other)
            for person in card.persons:
                pid = names.get(person.lower())
                if pid and pid != card.id:
                    card.links_out.add(pid)
            card.links_out |= self._topics_of(card)
            if card.type == ARCHIVE and (lst := self._list_of(card.path)):
                card.links_out.add(lst)
        for card in cards:
            for other in card.links_out:
                if other in self.cards:
                    self.cards[other].links_in.add(card.id)

    def _topics_of(self, card: Card) -> set[str]:
        """The ids of the topics `card` belongs to; none for a topic.

        The topic of the folder it is filed in (`homer/camping/notes/...`),
        and the topics it names: a list names those it serves. `topics`
        names a folder by its slug alone, so the card's own bucket is looked
        in first, then the card the old name `topic/<slug>` opens. A tag
        names a topic a topic card declares by its id, the topic of the
        card's own bucket and no other; a tag the ontology has is the
        ontology's topic, never a folder's.
        """
        if card.type == "topic":
            return set()
        bucket, _, rest = card.path.partition("/")
        folder, _, filed = rest.partition("/")
        found = {self._topic(bucket, folder)} if filed else set()
        for slug in card.meta.get("topics") or []:
            found.add(self._topic(bucket, str(slug)) or self._named.get(f"topic/{slug}", ""))
        for tag in tags_of(card):
            if tag in self.ontology.declared_topics:
                found.add(self._topic(bucket, tag))
        return found - {""}

    def _topic(self, bucket: str, folder: str) -> str:
        """The id of the topic card of `folder` in `bucket`, "" for a folder that is no topic."""
        for name in TOPIC_CARDS:
            cid = self._by_path.get(f"{bucket}/{folder}/{name}", "")
            if cid and self.cards[cid].type == "topic":
                return cid
        return ""

    def _list_of(self, archive: str) -> str:
        """The id of the list whose monthly archive is at `archive`, in the
        archive's bucket: `homer/lists/archive/markt/2026-10.md` is the
        archive of `homer/lists/markt.md`. "" when that list is gone."""
        parts = archive.split("/")
        return self._by_path.get(list_path(parts[-2], bucket=parts[0]), "")


def _aliases(cards: list[Card]) -> dict[str, str]:
    """The names a card had before (`topic/camping`, `note-0a1b2c3d4e`), to its id.

    Buckets can share a name from before card ids: the family's camping
    folder and Homer's were both `topic/camping`. The name opens the card
    whose id it spells, `topic-camping`, the first card given an id of
    that slug, since the next one is numbered (`topic-camping-2`). A name
    no card id spells opens the first card that has it, in path order
    (`cards` comes so).

    An id a card had before its current one (`former_card_ids`, ADR-016
    §2) opens it too: the first card in path order that lists it, as
    `stack.cards.lookup.find_by_card_id` answers. An id a card carries as
    its own now is that card's and never an alias.
    """
    spelled = [c for c in cards if c.name and c.id == c.name.replace("/", "-")]
    named: dict[str, str] = {}
    for c in [*spelled, *cards]:
        named.setdefault(c.name or c.id, c.id)
    current = {c.id for c in cards}
    for c in cards:
        for former in former_card_ids(c.meta):
            if former not in current:
                named.setdefault(former, c.id)
    return named


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
    ontology = get_ontology(vault)
    compiled = _views(Path(views)) if views is not None else {}
    cards = []
    for path in sorted(vault.rglob("*.md")):
        rel = path.relative_to(vault).as_posix()
        if rel.startswith(".git/") or "/.git/" in rel or is_navigation_page(rel):
            continue
        if audience is not None and not readable(rel, audience, shared_bucket=shared_bucket):
            continue
        data = path.read_bytes()
        card = _card(rel, data.decode("utf-8", errors="replace"))
        card.blob = card_version(data)
        card.tag_labels = tuple(w for tag in tags_of(card) for w in tag_words(tag, ontology))
        card.changes = changes.get(rel) or []
        if not card.day and card.changes:
            card.day = card.changes[0].day
        card.views = [v for v in _views_of(card, compiled)
                      if audience is None or readable(v.path, audience, shared_bucket=shared_bucket)]
        card.sidecar = _sidecar(path)
        cards.append(card)
    return Graph(cards, ontology, _lone_sidecars(vault) if audience is None else [])


def _sidecar(card_file: Path) -> dict | None:
    """The sidecar beside `card_file`, None for none; {} for one that does not parse."""
    side = card_file.with_suffix(SIDECAR_SUFFIX)
    if not side.is_file():
        return None
    try:
        loaded = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _lone_sidecars(vault: Path) -> list[str]:
    """Card sidecars whose card is gone (ADR-018 §3): a `.json` beside no `.md`.

    The media archive keeps its own sidecars and is no card's.
    """
    lone = []
    for side in sorted(vault.rglob(f"*{SIDECAR_SUFFIX}")):
        rel = side.relative_to(vault).as_posix()
        if ".git" in side.relative_to(vault).parts or rel.startswith("media/"):
            continue
        if not side.with_suffix(".md").is_file():
            lone.append(rel)
    return lone


@dataclass(frozen=True)
class _Compiled:
    """A view in the brain, with what it says about the cards it names."""

    view: View
    about: str
    versions: dict


def _views(brain: Path) -> dict[str, list[_Compiled]]:
    """Every view in `brain`, by each card id it names (`about`, `derived_from`)."""
    found: dict[str, list[_Compiled]] = {}
    for file in sorted(brain.rglob("*.md")):
        rel = file.relative_to(brain).as_posix()
        if rel.startswith(".git/"):
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("---"):
            continue
        try:
            meta = parse(text)
        except FrontmatterError:
            continue
        if not str(meta.get(VIEW) or "").strip():
            continue
        body = _ANSWER.sub("", _GENERATED.sub("", _FRONTMATTER.sub("", text, count=1))).strip()
        view = View(view=str(meta[VIEW]), title=str(meta.get("title") or ""), path=rel, text=body)
        entry = _Compiled(view, str(meta.get(ABOUT) or ""), compiled_versions(meta))
        for card_id in view_names(meta):
            found.setdefault(card_id, []).append(entry)
    return found


def _views_of(card: Card, compiled: dict[str, list[_Compiled]]) -> list[View]:
    """The views that name `card`, the one about it first, each marked when behind it."""
    card_id = card_id_from(card.meta)
    if not card_id:
        return []
    named = sorted(compiled.get(card_id, []), key=lambda c: (c.about != card_id, c.view.path))
    out = []
    for c in named:
        version = c.versions.get(card_id, "")
        behind = bool(version) and version != card.blob
        out.append(View(c.view.view, c.view.title or card.title, c.view.path, c.view.text, behind))
    return out


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
    name_kind, value = card_name(rel, meta)
    # Capture ids are Matrix event ids: shown by a unique prefix, as git shows a commit.
    name = f"{name_kind}/{value.lstrip('$')[:_SHORT_ID] if name_kind in ('capture', 'diary') else value}"
    ref_kind, ref = card_ref(rel, meta)
    card_id = ref if ref_kind == CARD_ID else name
    title = str(meta.get("title") or Path(rel).stem).strip()
    day = str(meta.get("date") or "")[:10] or str(meta.get("timestamp") or "")[:10]
    # A person goes by their id too: a record's `persons` holds ids (ADR-017 §4).
    names = tuple(n.lower() for n in [
        value, meta.get("slug"), meta.get("canonical"), meta.get("title"),
        *(meta.get("synonyms") or []), *(meta.get("aliases") or []),
        str(meta.get("title") or "").split(" ")[0],
    ] if n) if kind == "person" else ()
    persons = tuple(str(p) for p in (meta.get("persons") or []) if p)
    return Card(id=card_id, type=kind, title=title, path=rel, day=day, name=name,
                text=_GENERATED.sub("", text), names=names, persons=persons, meta=dict(meta))


def tags_of(card: Card) -> list[str]:
    """`card`'s tags as written; none when it has no list of them."""
    tags = card.meta.get("tags")
    return [str(t) for t in tags if t] if isinstance(tags, list) else []


def links_of(card: Card) -> list[tuple[str, str]]:
    """(the link as written, the vault path it points at) for each link in
    `card`'s text to another card. Links to media and to the web are not."""
    found = []
    for link in _LINK.findall(card.text):
        if target := _page(link, card.path):
            found.append((link, target))
    return found


def media_of(card: Card) -> set[str]:
    """The files of the media archive `card` embeds or links, as paths inside
    the archive (`2026/10/x.png`)."""
    found = set()
    for link in _EMBED.findall(card.text) + _FILE_LINK.findall(card.text):
        link = link.strip()
        path = link.lstrip("/") if link.startswith("/") else \
            posixpath.normpath(posixpath.join(posixpath.dirname(card.path), link))
        if path.startswith(_MEDIA) and ".." not in path.split("/"):
            found.add(path.removeprefix(_MEDIA))
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
    title = card.title.lower()
    # A topic tag is found by its label in any language, as well as by its id.
    text = "\n".join((card.text, *card.tag_labels)).lower()
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
