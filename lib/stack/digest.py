"""Signed cards: what a compile wrote, and whether a person changed it since.

A card the stack compiles from a source (a diary card from its messages)
is signed: a `digest:` line in its frontmatter holds a hash of the rest
of the file. A file that still matches its digest is the compile's, and
the next compile may write it again. A file without a digest, or whose
text no longer matches it, was written or changed by a person and is
theirs from then on: nothing rewrites it.

    text = signed(card_text)
    changed_by_hand(text)                      # False
    changed_by_hand(text.replace("a", "b"))    # True

The compile signs what it writes; a migration that changes the format of
a card the compile still owns signs it again, so the card stays the
compile's. Pure functions, stdlib only: the host CLI's migrations and the
bots both import it.
"""

from __future__ import annotations

import hashlib
import re

from .frontmatter import parse

DIGEST = "digest"

# The lines a digest does not cover: its own, and the card id, which the
# stack gives a card that has none without making it a person's.
_UNSIGNED_LINES = re.compile(r"^(?:digest|card_id): .*\n", re.MULTILINE)
_DIGEST_LINE = re.compile(r"^digest: .*$", re.MULTILINE)
_CLOSING = "\n---\n"

# Sixteen hex digits: enough to tell an edit apart, short enough to read.
_DIGITS = 16


def digest_of(text: str) -> str:
    """A hash of the file's content, blind to the digest line itself, the
    card id, and trailing whitespace an editor may add or trim on save."""
    unsigned = _UNSIGNED_LINES.sub("", text)
    normal = "\n".join(ln.rstrip() for ln in unsigned.splitlines()).strip()
    return hashlib.sha256(normal.encode("utf-8")).hexdigest()[:_DIGITS]


def signed(text: str) -> str:
    """The file signed with the digest of its content, every other byte as it was.

    A digest line it has is replaced where it is; a file without one gets
    it last in its frontmatter.
    """
    line = f"{DIGEST}: {digest_of(text)}"
    head, sep, rest = text.partition(_CLOSING)
    if _DIGEST_LINE.search(head):
        return _DIGEST_LINE.sub(lambda _: line, head, count=1) + sep + rest
    return f"{head}\n{line}{sep}{rest}"


def changed_by_hand(text: str) -> bool:
    """Whether a person has written or changed this file since the compile signed it.

    A file without a digest was written by a person (or corrected by one
    and written unsigned), and a file whose content no longer matches its
    digest was changed after the compile wrote it.
    """
    stored = str(parse(text).get(DIGEST) or "")
    return not stored or stored != digest_of(text)
