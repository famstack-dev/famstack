"""How a record links the cards it names (ADR-017 §3, §4, §10).

A record names people, a correspondent and, filed into a topic, a topic.
Each of them is a card in the memory repo, and the record links that
card's own file. The frontmatter holds the ids, the body holds the links
derived from them:

    persons:
      - homer
    > **About** [Homer](../../../people/homer.md)

One core, two surfaces. The archivist writes new records in this shape,
and the migrations bring existing files to it (`memory/0003` names people
by id, `memory/0004` declares correspondents and topics). Both call the
functions here, so a file a migration fixed reads like one written today
and a second pass finds nothing to change.

- `About`: the names a classifier gave, as person ids and as links. A name
  no declared person goes by stays a plain name and is no id.
- `without_person_tags`: tags without `Person: <name>`; `persons` says who.
- `correspondent_card`, `topic_card`: the card a correspondent or a topic
  gets the first time something names it, written once, never rewritten.
- `people_by_id`, `moved_links`, `with_correspondent_type`: one existing
  file brought to that shape, only the lines the rule is about.

Pure functions, text in and text out. Stdlib only.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from .cards import CARD_ID, new_card_id
from .frontmatter import FrontmatterError, dump, parse, with_field
from .people import Person, person_path, resolve
from .vault import correspondent_path, relative_link, topic_card_path


# A tag that names a person the way Paperless does, `Person: Homer`, in
# any spacing or case a hand edit gives it.
_PERSON_TAG = re.compile(r"^\s*person\s*:", re.IGNORECASE)

# A markdown link: its label and where it points.
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")

# A person's compiled page in the brain, `<id>/about.md`: the target
# records linked before people had cards.
_PERSON_PAGE = re.compile(r"^([^/]+)/about\.md$")

# A document's file name ends in its Paperless id: `...-p72.md`, `p72.md`.
_PAPERLESS_FILE = re.compile(r"(?:^|-)p(\d+)\.md$")

# The record types whose people these rules name. A diary card is one too,
# but it is signed with a digest over its text (`diary_card.render`): an
# edit from outside the compile makes it the family's for good.
LINKED_RECORDS = frozenset({"document", "note", "bookmark", "email"})


# ── People ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class About:
    """Who a record is about: the ids it stores, the links it shows, and the
    names no declared person goes by.

        >>> about = About.of(["Homer", "Mr. Burns"], [Person("homer", "Homer Simpson")])
        >>> about.ids, about.unknown
        (['homer'], ['Mr. Burns'])
        >>> about.links("family/documents/2026/03/x.md", bucket="family")
        '[Homer](../../../people/homer.md), Mr. Burns'

    Re-rendered from a file's `persons`, the names are ids already, and a
    link reads the id with a capital: `[Homer](...)`.
    """

    names: tuple[tuple[str, str], ...] = ()  # (the name as given, its person's id or "")

    @classmethod
    def of(cls, names: Iterable[str], people: list[Person] | None) -> "About":
        """The names a classifier gave, each resolved through the declared `people`."""
        known = people or []
        found: list[tuple[str, str]] = []
        for name in names:
            name = " ".join(str(name).split())
            if not name or name in (n for n, _ in found):
                continue
            person = resolve(name, known)
            found.append((name, person.id if person else ""))
        return cls(tuple(found))

    @property
    def ids(self) -> list[str]:
        """The `persons` field: each resolved person's id, once, in order."""
        return list(dict.fromkeys(pid for _, pid in self.names if pid))

    @property
    def unknown(self) -> list[str]:
        """The names no declared person goes by."""
        return [name for name, pid in self.names if not pid]

    def links(self, from_path: str, *, bucket: str) -> str:
        """The names as the meta block shows them: a link to each person's
        card, a plain name for anyone else."""
        return ", ".join(
            f"[{self._label(name, pid)}]({relative_link(person_path(pid, bucket), from_path)})"
            if pid else name for name, pid in self.names)

    @staticmethod
    def _label(name: str, pid: str) -> str:
        """A link's label: the name as given, or a readable one for a bare id."""
        return name[:1].upper() + name[1:] if name == pid else name

    def __bool__(self) -> bool:
        return bool(self.names)


def without_person_tags(tags: Iterable[str]) -> list[str]:
    """`tags` without the `Person: <name>` ones (ADR-017 §4).

    >>> without_person_tags(["Insurance", "Person: Homer", "person:Bart"])
    ['Insurance']
    """
    return [t for t in tags if not _PERSON_TAG.match(str(t))]


# ── Correspondents and topics ─────────────────────────────────────────────

def correspondent_card(name: str, aliases: Iterable[str] = (), *, bucket: str) -> tuple[str, str]:
    """(path, text) of the card a correspondent gets when a document first names it.

        >>> path, text = correspondent_card("Moe's Tavern", ["Moe's"], bucket="family")
        >>> path
        'family/correspondents/moe-s-tavern.md'
        >>> print(text, end="")
        ---
        type: correspondent
        card_id: correspondent-moe-s-tavern
        title: Moe's Tavern
        aliases:
          - Moe's
        ---
        <BLANKLINE>
        # Moe's Tavern
    """
    name = " ".join(name.split())
    path = correspondent_path(name, bucket)
    others = list(dict.fromkeys(a for a in (" ".join(str(x).split()) for x in aliases)
                                if a and a.lower() != name.lower()))
    fm = {"type": "correspondent", CARD_ID: new_card_id("correspondent", path),
          "title": name, "aliases": others}
    return path, f"---\n{dump(fm)}\n---\n\n# {name}\n"


def topic_title(topic: str) -> str:
    """A topic's title read from its folder's slug.

    >>> topic_title("itchy-scratchy-land")
    'Itchy Scratchy Land'
    """
    return " ".join(w[:1].upper() + w[1:] for w in topic.split("-") if w)


def topic_card(bucket: str, topic: str) -> tuple[str, str]:
    """(path, text) of the card a topic gets when something is first filed into it.

        >>> topic_card("family", "itchy-scratchy-land")[0]
        'family/itchy-scratchy-land/topic.md'
    """
    path = topic_card_path(bucket, topic)
    title = topic_title(topic)
    fm = {"type": "topic", CARD_ID: new_card_id("topic", path), "title": title}
    return path, f"---\n{dump(fm)}\n---\n\n# {title}\n"


# ── Existing files ────────────────────────────────────────────────────────

def people_by_id(text: str, path: str, people: list[Person], *, bucket: str) -> tuple[str, list[str]]:
    """The record at `path` with its people named by id, and the names no one goes by.

    Each `persons` entry a declared person goes by becomes their id; the
    `Person: <name>` tags go; in the meta block under the title, each link
    to a person's compiled page (`<id>/about.md`) points at their card. A
    name nobody goes by stays as it is, link included, and is returned.
    Every other byte stays as it was. Anything but a linked record
    (`LINKED_RECORDS`) comes back unchanged.
    """
    try:
        fields = parse(text)
    except FrontmatterError:
        return text, []
    if str(fields.get("type") or "") not in LINKED_RECORDS:
        return text, []
    unknown: list[str] = []

    persons = fields.get("persons")
    if isinstance(persons, list):
        named = []
        for name in persons:
            person = resolve(str(name), people)
            if person is None:
                unknown.append(str(name))
            named.append(person.id if person else str(name))
        named = list(dict.fromkeys(named))
        if named != persons:
            text = with_field(text, "persons", named)

    tags = fields.get("tags")
    if isinstance(tags, list) and (kept := without_person_tags(tags)) != tags:
        text = with_field(text, "tags", kept)

    def card_of(match: re.Match) -> str:
        label, href = match[1], match[2]
        page = _PERSON_PAGE.match(_target(href, path))
        if page is None:
            return match[0]
        person = resolve(label, people) or next((p for p in people if p.id == page[1]), None)
        if person is None:
            unknown.append(label)
            return match[0]
        return f"[{label}]({relative_link(person_path(person.id, bucket), path)})"

    text = _in_meta_block(text, lambda line: _LINK.sub(card_of, line))
    return text, list(dict.fromkeys(unknown))


def moved_links(text: str, path: str, *, exists: Callable[[str], bool],
                documents: Mapping[str, list[str]]) -> tuple[str, list[str]]:
    """The file at `path` with each link to a document that moved pointing at
    where it is now, and the links it could not place.

    A link whose target is no file and names a document by its Paperless
    id (`...-p72.md`) is rewritten when `documents` (Paperless id to the
    paths of the documents carrying it) holds exactly one file for that
    id. None or two: the link stays as it is and is returned. Every
    other byte stays as it was.
    """
    left: list[str] = []

    def moved(match: re.Match) -> str:
        label, href = match[1], match[2]
        target = _target(href, path)
        if not target or exists(target):
            return match[0]
        source = _PAPERLESS_FILE.search(target.rsplit("/", 1)[-1])
        if source is None:
            return match[0]
        found = documents.get(source[1], [])
        if len(found) != 1:
            left.append(f"{path}: {href} ({len(found)} documents with Paperless id {source[1]})")
            return match[0]
        return f"[{label}]({relative_link(found[0], path)})"

    return _LINK.sub(moved, text), left


def with_correspondent_type(text: str, path: str) -> str:
    """A correspondent file of the old shape (`kind: correspondent`, no `type`)
    with `type: correspondent` and its card id first in its frontmatter,
    nothing else changed. Any other file comes back as it is.
    """
    try:
        fields = parse(text)
    except FrontmatterError:
        return text
    if fields.get("type") or fields.get("kind") != "correspondent":
        return text
    added = dump({"type": "correspondent", CARD_ID: new_card_id("correspondent", path)})
    return f"---\n{added}\n{text[4:]}"


def _target(href: str, here: str) -> str:
    """The vault path a link in the file at `here` points at; "" for a link
    out of the vault (the web, an anchor, a media file)."""
    if "://" in href or href.startswith(("#", "mailto:")) or not href.endswith(".md"):
        return ""
    if href.startswith("/"):
        return href.lstrip("/")
    target = posixpath.normpath(posixpath.join(posixpath.dirname(here), href))
    return "" if target.startswith("..") else target


def _in_meta_block(text: str, edit: Callable[[str], str]) -> str:
    """`text` with `edit` applied to each line of the meta block: the quoted
    lines right under the title (`> **From:** ... · **About:** ...`)."""
    lines = text.split("\n")
    at = _after_frontmatter(lines)
    while at < len(lines) and not lines[at].startswith("# "):
        at += 1
    at += 1
    while at < len(lines) and not lines[at].strip():
        at += 1
    while at < len(lines) and lines[at].startswith(">") and not lines[at].startswith("> [!"):
        lines[at] = edit(lines[at])
        at += 1
    return "\n".join(lines)


def _after_frontmatter(lines: list[str]) -> int:
    if not lines or lines[0] != "---":
        return 0
    for i in range(1, len(lines)):
        if lines[i] == "---":
            return i + 1
    return len(lines)
