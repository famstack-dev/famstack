"""Find where a card lives right now, by its card id.

`/go/capture/<id>` and `/go/diary/<id>` promise a link that survives the
file moving, and a correction to a capture has to find the file it is
about after a refile or a move. Every other logical path can be resolved
by rewriting the string, but these cannot: the id names *which* card,
and says nothing about where it is today. Something has to look, and
this module is the one place that does (`stack.cards` says which field
holds each kind's id).

WHY A SCAN AND NOT AN INDEX

The obvious design is an index the curator maintains during the mirror.
It would be faster and it would be one more thing that can be wrong: an
index is a second copy of the truth, so it can go stale, disagree with
the tree, or be missing on a fresh clone, and every one of those failures
looks like a dead link.

A scan reads the same files the wiki serves, so it cannot disagree with
them, and there is nothing to rebuild or invalidate. A family vault is
hundreds to low thousands of small markdown files and a lookup happens
when a human clicks something or replies to a filing, so the cost lands
in the right place. If a vault ever grows past that, an index can be
added *behind this same function* without touching its callers or the
link format.

WHY FRONTMATTER AND NOT THE BODY

Card ids are frontmatter fields, and the search path already treats
frontmatter as the machine-readable half of a page. Reading only the
frontmatter keeps a scan cheap and avoids matching an id someone quoted
in prose. It is parsed with `stack.frontmatter`, the parser every writer
of these files uses, so a file whose frontmatter it rejects (a hand edit
gone wrong) answers to no id rather than to a guess.
"""

from __future__ import annotations

from pathlib import Path

from stack.cards import CARD_ID_FIELDS
from stack.frontmatter import FrontmatterError, parse

_DELIMITER = "---"


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


def find_card(kind: str, card_id: str, *, brain_dir: Path) -> str | None:
    """Vault-relative path (`.md` included) of the card of `kind` with `card_id`.

    `kind` is a key of `stack.cards.CARD_ID_FIELDS` (`capture`, `diary`).
    The path is the same in the vault and in the brain projection the
    wiki serves, so a caller can read the file from either.

    Returns None rather than a guess for an unknown kind or an id no card
    carries: a 404 tells the person the card is gone, while a redirect to
    something else would quietly show them the wrong note.
    """
    field = CARD_ID_FIELDS.get(kind)
    wanted = (card_id or "").strip()
    brain_dir = Path(brain_dir)
    if field is None or not wanted or not brain_dir.is_dir():
        return None
    for path in sorted(brain_dir.rglob("*.md")):
        rel = path.relative_to(brain_dir)
        if ".git" in rel.parts:
            continue
        if str(_frontmatter_of(path).get(field) or "").strip() == wanted:
            return rel.as_posix()
    return None
