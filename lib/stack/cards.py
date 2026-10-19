"""Card ids: the one value that names a card for its whole life.

A card is one file in the family memory that stands for one thing. Its
path is not a name: the folder follows the bucket and the topic, the
file name often follows the title, and all of those change under
ordinary use. Each card type already carries a field that never
changes, under a name of its own, and that field is the card's id.

This module holds the part of that table the link resolver needs: the
cards it finds by reading the vault. Documents are not here because a
document link opens Paperless, which is keyed by `paperless_id` already;
people, topics and lists are linked by their names. The full table,
every type included, is in `docs/stack-reference.md`.

It also holds the one lookup by card id (`find_card`). Every reader runs
it over the copy of the family memory it reads from: core's tools server
over the wiki's copy, so a `/go/` link lands on a page the wiki serves;
the archivist over its own mount, before it corrects a capture. One
implementation, and no reader waits on another's copy to catch up.

Every card also carries an id of its own, `card_id` (ADR-016 §2):
`<type>-<value>`, made once when the card is first written and never
derived again. `new_card_id` makes it, for the writers, the migration
that gave existing cards theirs, and the curator, which gives one to a
file written by hand (`with_card_id`). Readers take it from
`card_id_from`. A card that changes its type gets a new id and keeps the
ones it had in `former_card_ids` (`card_identity`). Until every card carries an id, the
table above and `find_card` keep resolving the links in use.

Stdlib only: the host CLI, the bot-runner and core's tools server all
import it, so emitter and resolver read one table.

WHY A SCAN AND NOT AN INDEX

An index the curator maintains would be faster, and it would be one more
thing that can be wrong: a second copy of the truth that can go stale,
disagree with the tree or be missing on a fresh clone, and every one of
those failures looks like a dead link. A scan reads the files themselves.
A family vault is hundreds to low thousands of small markdown files and a
lookup happens when a person clicks a link or replies to a filing, so the
cost lands in the right place. An index can be added behind `find_card`
later without touching its callers or the link format.

Only frontmatter counts: it is the machine-readable half of a page, and
an id someone quoted in prose must not make that page answer to it. It
is parsed with `stack.frontmatter`, the parser every writer of these
files uses, so a file whose frontmatter it rejects (a hand edit gone
wrong) answers to no id rather than to a guess.
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path, PurePosixPath
from typing import Container, Mapping

from .frontmatter import RECORD_TYPES, FrontmatterError, dump, parse
from .vault import slug


# ── The table ─────────────────────────────────────────────────────────
#
# Keyed by the link kind (`/go/<kind>/<id>`), valued by the frontmatter
# field that holds the id. `capture` covers notes and bookmarks: both
# carry the event id of the message they were filed from.

CARD_ID_FIELDS: dict[str, str] = {
    "capture": "capture_id",
    "diary": "entry_id",
}


def card_id_of(fields: Mapping[str, object]) -> tuple[str, str] | None:
    """(kind, card id) for a card's frontmatter, or None.

    None for a page the resolver does not find by id: a topic, a person,
    a list, a document, or a card whose id field is empty.

    >>> card_id_of({"type": "note", "capture_id": "$abc"})
    ('capture', '$abc')
    >>> card_id_of({"type": "diary", "entry_id": "3f9a0c1b2d"})
    ('diary', '3f9a0c1b2d')
    >>> card_id_of({"type": "list", "title": "Einkaufsliste"}) is None
    True
    """
    for kind, field in CARD_ID_FIELDS.items():
        value = str(fields.get(field) or "").strip()
        if value:
            return kind, value
    return None


# ── Every card's kind and id ─────────────────────────────────────────
#
# The table above is what the `/go` resolver finds by reading the vault.
# The rule below names every card, whatever its kind, for the readers that
# see the whole memory (the card graph, the pages that show it): one id
# per thing, so a person, a list or a correspondent is one card however a
# link reaches it.

# The kinds a card can be, besides what its `type` says: a list's monthly
# archive, and a page the wiki generates to navigate (a diary month, an index).
ARCHIVE = "list-archive"
INDEX = "index"
# Navigation pages for Forgejo and the wiki, wherever they are: never cards
# (ADR-017 §6).
NAVIGATION_PAGES = frozenset({"README.md", "index.md"})
# Records that carry the id of the message they were filed from, when they do.
_CAPTURED = ("note", "bookmark", "email")


def is_navigation_page(rel: str) -> bool:
    """Whether the file at `rel` (vault-relative path) is a navigation page, no card.

    >>> is_navigation_page("family/correspondents/README.md")
    True
    >>> is_navigation_page("family/notes/index-cards.md")
    False
    """
    return rel.rsplit("/", 1)[-1] in NAVIGATION_PAGES


def card_kind(rel: str, fields: Mapping[str, object]) -> str:
    """The kind of the card at `rel` (vault-relative path) with `fields`."""
    parts = rel.removesuffix(".md").split("/")
    if len(parts) >= 4 and parts[1] == "lists" and parts[2] == "archive":
        return ARCHIVE
    kind = str(fields.get("type") or "").strip()
    if kind:
        return kind
    if len(parts) == 3 and parts[1] == "lists":
        return "list"
    if str(fields.get("generated") or "").lower() == "true":
        return INDEX
    return "page"


def card_ref(rel: str, fields: Mapping[str, object]) -> tuple[str, str]:
    """(kind of id, id) of the card at `rel`: `("person", "homer")`.

    The id is the field that never changes where the kind has one, the
    thing's own name otherwise (a slug, the list's file), and the path
    only for a page that is nothing else.

    >>> card_ref("family/people/homer.md", {"type": "person"})
    ('person', 'homer')
    >>> card_ref("homer/about.md", {"type": "person", "generated": "true"})
    ('person', 'homer')
    >>> card_ref("family/lists/archive/markt/2026-10.md", {"type": "list"})
    ('list', 'markt/2026-10')
    """
    stable = card_id_of(fields)
    paperless = str(fields.get("paperless_id") or "").strip()
    if stable:
        return stable
    if paperless:
        return "docs", paperless
    parts = rel.removesuffix(".md").split("/")
    kind = card_kind(rel, fields)
    slug = str(fields.get("slug") or "").strip()
    if kind == ARCHIVE:
        return "list", f"{parts[-2]}/{parts[-1]}"
    if kind == "list":
        return "list", parts[-1]
    if kind == "person":
        return "person", slug or str(fields.get("account") or "").strip() or \
            (parts[0] if parts[-1] == "about" else parts[-1])
    if kind == "topic":
        return "topic", slug or (parts[-2] if parts[-1] == "about" else parts[-1])
    if kind == "correspondent":
        return "correspondent", slug or parts[-1]
    if kind == "email" and str(fields.get("resource") or "").strip():
        return "email", str(fields["resource"]).strip()  # the thread's first message, mid:…
    if kind in _CAPTURED:
        return kind, parts[-1]  # written by hand: no message to name it by
    if kind == INDEX:
        return INDEX, "/".join(parts)
    return "page", "/".join(parts)


# ── A card's own id ───────────────────────────────────────────────────
#
# A record's value is random, because nothing about a record is both
# stable and ours: Paperless numbers documents anew on a re-import, and a
# Matrix event id belongs to Matrix. Any other card's value is its slug,
# the name it already goes by.

CARD_ID = "card_id"
FORMER_CARD_IDS = "former_card_ids"

# The closed vocabulary of `type` (ADR-017 §6).
CARD_TYPES = frozenset({
    "document", "note", "bookmark", "email", "diary", "list", "person", "correspondent", "topic",
})

# What a file that says nothing of what it is was written as: a note by hand.
HAND_WRITTEN = "note"

# Ten hex digits, the length ADR-016 gives a record's id.
_RANDOM_BYTES = 5

# A list's archive for one month, as `stack.lists.archive_path` names it.
_ARCHIVE = re.compile(r"(?:^|/)lists/archive/(?P<list>[^/]+)/(?P<month>\d{4}-\d{2})\.md$")

# A file name that is an id already, as `stack.people.person_id` reads one.
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


_OPENING = "---\n"
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_TYPE_LINE = re.compile(r"^type:.*\n", re.MULTILINE)
_CARD_ID_LINE = re.compile(r"^card_id:.*\n", re.MULTILINE)


class CardIdError(ValueError):
    """A file `with_card_id` cannot give an id without guessing what it is."""


def card_id_from(fields: Mapping[str, object]) -> str:
    """The card's own id from its frontmatter, "" while it has none.

    >>> card_id_from({"type": "note", "card_id": "note-7f3a9c21e4"})
    'note-7f3a9c21e4'
    >>> card_id_from({"type": "note", "capture_id": "$abc"})
    ''
    """
    return str(fields.get(CARD_ID) or "").strip()


def former_card_ids(fields: Mapping[str, object]) -> list[str]:
    """The ids a card had before its current one, oldest first.

    >>> former_card_ids({"card_id": "note-0b6e2d91aa", "former_card_ids": ["bookmark-7f3a9c21e4"]})
    ['bookmark-7f3a9c21e4']
    """
    value = fields.get(FORMER_CARD_IDS) or []
    values = value if isinstance(value, list) else [value]
    return [str(v).strip() for v in values if str(v).strip()]


def card_identity(previous: Mapping[str, object], card_type: str, path: str) -> dict[str, object]:
    """The id fields of a card written as `card_type` at `path`.

    `previous` is the frontmatter of the file the write replaces, {} for
    a new card. A rebuild keeps the card's id and its former ids. A card
    whose type changes is a new card (ADR-016): it gets a new id of its
    new type and lists the one it had in `former_card_ids`, so a link to
    the old id still finds it.

    >>> card_identity({"card_id": "note-7f3a9c21e4"}, "note", "marge/notes/a.md")
    {'card_id': 'note-7f3a9c21e4'}
    >>> retyped = card_identity({"card_id": "bookmark-7f3a9c21e4"}, "note", "marge/notes/a.md")
    >>> retyped["card_id"].startswith("note-"), retyped["former_card_ids"]
    (True, ['bookmark-7f3a9c21e4'])
    """
    current, former = card_id_from(previous), former_card_ids(previous)
    if current.startswith(f"{card_type}-"):
        identity: dict[str, object] = {CARD_ID: current}
    else:
        identity = {CARD_ID: new_card_id(card_type, path)}
        if current and current not in former:
            former = [*former, current]
    if former:
        identity[FORMER_CARD_IDS] = former
    return identity


def new_card_id(card_type: str, path: str, *, taken: Container[str] = ()) -> str:
    """The id a card of `card_type` gets when it is first written at `path`.

    A record (document, note, bookmark, email, diary) gets ten random hex
    digits. Any other card gets its file's slug, and a list's archive its
    list's slug and month. `taken` holds the ids other cards carry: a
    slug two buckets share gets a number after it the second time.

    >>> new_card_id("person", "family/people/homer.md")
    'person-homer'
    >>> new_card_id("list", "family/lists/archive/markt/2026-10.md")
    'list-markt-2026-10'
    >>> new_card_id("list", "homer/lists/markt.md", taken={"list-markt"})
    'list-markt-2'
    """
    if card_type in RECORD_TYPES:
        while (card_id := f"{card_type}-{secrets.token_hex(_RANDOM_BYTES)}") in taken:
            pass
        return card_id
    archive = _ARCHIVE.search(path) if card_type == "list" else None
    if archive:
        value = f"{_slug(archive['list'])}-{archive['month']}"
    else:
        value = _slug(PurePosixPath(path).stem)
    card_id, n = f"{card_type}-{value}", 1
    while card_id in taken:
        n += 1
        card_id = f"{card_type}-{value}-{n}"
    return card_id


def with_card_id(text: str, path: str, card_id: str = "", *,
                 taken: Container[str] = ()) -> str:
    """The file at vault path `path` with its card id, nothing else changed.

    A card that carries an id keeps it, and one without gets a new one
    (`new_card_id`, clear of `taken`). Given `card_id`, the card gets that
    one in place of any it carries: how a copy is told apart from the card
    it was copied from, and how a rewrite that dropped the id gets it back.

    A file that says nothing of what it is, with no frontmatter or no
    `type`, was written by hand and is a note (ADR-017 §6). It gets
    `type: note`, the id, and a `title` from its first heading or else its
    file name when it has none. Only the lines added are new; every other
    byte of the file stays as it was.

    A page that is no card comes back as it is: a navigation page, a file
    in a hidden folder, a view the wiki compiled. `CardIdError` when the
    rule cannot place the file without guessing: its frontmatter does not
    parse, its `type` is no card type, or it has a `kind` and no `type`.
    """
    if not _may_be_card(path):
        return text
    if not text.startswith("---"):
        return _as_note("", text, path, {}, card_id, taken)
    end = text.find("\n---\n", 3)
    if not text.startswith(_OPENING) or end < 0:
        raise CardIdError("its frontmatter has no closing `---` line")
    try:
        fields = parse(text)
    except FrontmatterError as e:
        raise CardIdError(f"its frontmatter does not parse: {e}") from e
    if _is_view(fields):
        return text
    head, rest = text[:end + 1], text[end + 1:]
    card_type = str(fields.get("type") or "").strip()
    if not card_type:
        if str(fields.get("kind") or "").strip():
            raise CardIdError(f"it says `kind: {fields['kind']}` but has no `type`")
        return _as_note(head, rest, path, fields, card_id, taken)
    if card_type not in CARD_TYPES:
        raise CardIdError(f"`type: {card_type}` is no card type")

    current = card_id_from(fields)
    wanted = card_id or current or new_card_id(card_type, path, taken=taken)
    if wanted == current:
        return text
    line = f"{dump({CARD_ID: wanted})}\n"
    if current:
        return _CARD_ID_LINE.sub(lambda _: line, head, count=1) + rest
    after_type = _TYPE_LINE.search(head)
    at = after_type.end() if after_type else len(_OPENING)
    return head[:at] + line + head[at:] + rest


def _as_note(head: str, rest: str, path: str, fields: Mapping[str, object],
             card_id: str, taken: Container[str]) -> str:
    """A hand-written file as a note: the lines it lacks, first in its frontmatter.

    `head` is the frontmatter up to its closing line, "" for a file without
    any; `rest` is the closing line and what follows, or the whole file.
    """
    added = {"type": HAND_WRITTEN, CARD_ID: card_id or new_card_id(HAND_WRITTEN, path, taken=taken)}
    if "title" not in fields:
        heading = _HEADING.search(rest)
        added["title"] = heading[1] if heading else PurePosixPath(path).stem
    lines = f"{dump(added)}\n"
    if not head:
        return f"{_OPENING}{lines}{_OPENING}{rest}"
    return f"{_OPENING}{lines}{head[len(_OPENING):]}{rest}"


def _may_be_card(path: str) -> bool:
    """False for a path no card has: not markdown, a navigation page, hidden."""
    parts = PurePosixPath(path).parts
    return (path.endswith(".md") and parts[-1] not in NAVIGATION_PAGES
            and not any(part.startswith(".") for part in parts))


def _is_view(fields: Mapping[str, object]) -> bool:
    """A page the wiki compiled: it names cards and is none (ADR-016 §3)."""
    generated = fields.get("generated")
    return (generated is True or str(generated).strip().lower() == "true"
            or fields.get("type") == "index")


def _slug(name: str) -> str:
    """`name` as the value of an id: as it is when it is one already."""
    if _ID.match(name):
        return name
    return slug(name) or secrets.token_hex(_RANDOM_BYTES)


# ── The lookup ────────────────────────────────────────────────────────

_DELIMITER = "---"


def find_card(kind: str, card_id: str, *, root: Path | str) -> str | None:
    """Path of the card of `kind` with `card_id`, relative to `root`, or None.

    `root` is a copy of the family memory: the vault or the wiki's
    projection of it, whose paths are the same. The answer keeps the
    `.md` suffix.

    None rather than a guess for an unknown kind, an empty id, a missing
    `root`, or an id no card carries: a 404 tells the person the card is
    gone, while a redirect to something else would quietly show them the
    wrong note.
    """
    field = CARD_ID_FIELDS.get(kind)
    wanted = (card_id or "").strip()
    root = Path(root)
    if field is None or not wanted or not root.is_dir():
        return None
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root)
        if ".git" in rel.parts:
            continue
        if str(_frontmatter_of(path).get(field) or "").strip() == wanted:
            return rel.as_posix()
    return None


def _frontmatter_of(path: Path) -> dict:
    """The parsed frontmatter of a file, or {} when it has none that parses.

    Reads line by line up to the closing delimiter, so the cost of a scan
    is the size of the headers, not of the pages.
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            if fh.readline().rstrip("\r\n") != _DELIMITER:
                return {}
            block = [f"{_DELIMITER}\n"]
            for line in fh:
                if line.rstrip("\r\n") == _DELIMITER:
                    block.append(f"{_DELIMITER}\n")
                    return parse("".join(block))
                block.append(line)
    except (OSError, UnicodeDecodeError, FrontmatterError):
        return {}
    return {}
