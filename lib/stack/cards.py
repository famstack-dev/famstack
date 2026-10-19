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

from pathlib import Path
from typing import Mapping

from .frontmatter import FrontmatterError, parse


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
