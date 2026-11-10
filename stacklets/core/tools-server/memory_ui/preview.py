"""The line a chat app shows when it previews a link to a card.

A filing's answer in the chat links the card (`/go/card/<card id>`), and the
chat app shows a preview of the page the link lands on: its title and its
description. The card's page sets the description from the card's text,
as its type's renderer writes it (`stack.card_render`): the first
paragraph of prose, which is the summary of a card that has one (a
document's, an email's) and the first words of any other (a note's). The
wiki's `CardSummary` transformer gave the same preview while the link
landed there; the card's page now keeps it.

    description("Pitch 14, by the [lake](x). **Bring** the big tent.\\n\\n- Pegs")
    # 'Pitch 14, by the lake. Bring the big tent.'

A heading, a list, a quote, a table and an embed are not prose: they are
skipped. A card with no prose answers "", and the page then falls back to
its facts line.

Pure: no I/O.
"""

from __future__ import annotations

import re

__all__ = ["description"]

# Long enough for two sentences; a preview shows about three lines. The
# wiki's preview uses the same length.
MAX_CHARS = 300

# The first character of a block that is not prose.
_NOT_PROSE = ("#", "-", "*", "+", ">", "|", "!", "```")
_NUMBERED = re.compile(r"^\d+[.)]\s")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_WIKILINK = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`)")


def description(markdown: str) -> str:
    """The preview text of a card whose rendered body is `markdown`, "" when
    it has no paragraph of prose. Plain text, at most `MAX_CHARS`, cut at a
    word with " …".

        >>> description("## Facts\\n\\n- Room 12\\n\\nParents' evening on *Friday*.")
        "Parents' evening on Friday."
        >>> description("- [ ] Duff")
        ''
    """
    for block in re.split(r"\n\s*\n", markdown):
        text = block.strip()
        if text and not text.startswith(_NOT_PROSE) and not _NUMBERED.match(text):
            return _shortened(_plain(text))
    return ""


def _plain(text: str) -> str:
    """`text` without its markdown: links as their labels, no emphasis, one line."""
    text = _WIKILINK.sub(r"\1", _LINK.sub(r"\1", text))
    return " ".join(_EMPHASIS.sub("", text).split())


def _shortened(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    return text[:MAX_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + " …"
