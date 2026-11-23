"""How a note was made: the ✦ mark, the key line and the way to correct it.

A note's title and text are written once, when it is filed, by the AI
from the messages it was filed from (the sources, ADR-018). The sidecar
names the prompt that did it (`made.prompt_template`). A note filed while
the AI was down has sources and no prompt template, so its text is the
messages as sent and nothing here marks it.

    ai_made(card)                         # True: the sidecar names a prompt template
    ai_hint("external", language="en")    # "Summarised by your family's AI server"
    key_line(card, language="en", route="managed")   # "The title and the text are famstack's AI's, ..."
    correction(card, language="en")       # ("Something wrong?", "Reply in the thread ...")

The words name the AI the stack routes to (`memory_ui.ai_route`), so a
family with its own AI server is not told famstack's AI wrote its notes.
The card page and a topic's page both take ✦'s words from here.

Why it reads the sidecar and not the text: a card is rebuilt from its
sources, never from its own words (ADR-018), so what made it is what the
sidecar says. A diary card also carries a prompt template, and its page
stays as it is: only a note gets the mark.

Pure: no I/O. The card is the cards API's answer to `GET /card/<id>`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from html import escape

from memory_ui.ai_route import ROUTES
from memory_ui.words import words

__all__ = ["ai_made", "ai_hint", "key_line", "correction", "from_room", "mark"]

# The words of a note's own text an example correction starts with.
EXAMPLE_WORDS = 5
_MARKUP = re.compile(r"[>#*_`\[\]!]|\(.*?\)")


def ai_made(card: Mapping) -> bool:
    """Whether the AI wrote this note's title and text: a note whose sidecar
    names a prompt template.

        >>> ai_made({"type": "note", "made": {"prompt_template": "7c2e91a4"}})
        True
        >>> ai_made({"type": "note", "made": {}}), ai_made({"type": "diary", "made": {"prompt_template": "x"}})
        (False, False)
    """
    return card.get("type") == "note" and bool((card.get("made") or {}).get("prompt_template"))


def ai_hint(route: str = "", *, language: str) -> str:
    """What ✦ says on hover or tap, for the AI's `route` (`ai_route.of`):
    any other route names just an AI.

        >>> ai_hint("managed", language="en")
        "Summarised by famstack's AI"
        >>> ai_hint("", language="en")
        'Summarised by AI'
    """
    return words(language)["ai_hint"][_known(route)]


def key_line(card: Mapping, *, language: str, route: str = "") -> str:
    """The key under the facts: which AI wrote the title and the text
    (`route`, as for `ai_hint`) and that the messages are below. "" for a
    note the AI did not write.

        >>> card = {"type": "note", "made": {"prompt_template": "7c"},
        ...         "sources": [{"role": "original"}] * 6}
        >>> key_line(card, language="en", route="managed")[:41]
        "The title and the text are famstack's AI'"
    """
    if not ai_made(card):
        return ""
    n = sum(1 for s in card.get("sources") or [] if isinstance(s, Mapping) and s.get("role") == "original")
    one, many = words(language)["ai_key"][_known(route)]
    return (one if n == 1 else many).format(n=n)


def from_room(card: Mapping) -> bool:
    """Whether the card was filed from a chat room, so a reply in its thread
    corrects it.

        >>> from_room({"sources": [{"role": "original", "channel": "chat"}]})
        True
        >>> from_room({"sources": [{"role": "original", "channel": "cli"}]})
        False
    """
    return any(isinstance(s, Mapping) and s.get("role") == "original" and s.get("channel") == "chat"
               for s in card.get("sources") or [])


def correction(card: Mapping, *, language: str) -> tuple[str, str]:
    """The heading and the line that say how to correct a note filed from a
    room, with an example from its own text; ("", "") for any other note.

        >>> card = {"type": "note", "text": "Cut the strips 2.5 inches wide for binding.",
        ...         "sources": [{"role": "original", "channel": "chat"}]}
        >>> correction(card, language="en")[1].startswith("Reply in the thread")
        True
    """
    if card.get("type") != "note" or not from_room(card):
        return "", ""
    w = words(language)
    start = _start_of(str(card.get("text") or ""))
    example = w["correct_example"].format(start=start) if start else w["correct_example_plain"]
    return w["something_wrong"], w["correct_line"].format(example=example)


def mark(hint: str) -> str:
    """The ✦ as HTML with `hint` as its hover text; a tap focuses it.

        >>> mark("Summarised by famstack's AI")[:34]
        '<span class="ai-mark" tabindex="0"'
    """
    said = escape(hint)
    return f'<span class="ai-mark" tabindex="0" title="{said}" aria-label="{said}">✦</span>'


# ── Reading the note's text ───────────────────────────────────────────────

def _start_of(text: str) -> str:
    """The first few words of the note's first line of prose (no heading,
    no quote), without its markup: "Cut the strips 2.5 inches"; "" when it has none."""
    body = text.split("\n## Sources", 1)[0]
    for line in body.split("\n"):
        if line.lstrip().startswith(("#", ">", "---")):
            continue
        plain = _MARKUP.sub("", line).strip()
        if plain:
            return " ".join(plain.split()[:EXAMPLE_WORDS]).rstrip(".,:;")
    return ""


def _known(route: str) -> str:
    """`route` when it has words of its own, else "" (just an AI)."""
    return route if route in ROUTES else ""
