"""What an entry holds and the narrative around its sections.

An entry's page shows famstack's narrative (its title and summary) and,
under it, the sections its sources make (`sections.py`). This module
splits the two: `narrative` keeps what the sections do not show, `holds`
says what the sections hold for the facts line.

    holds(sections_of(card), language="en")   # 'Voice and 7 photos'
    narrative(card["text"])                   # the summary, without quotes and sources

Why the card's text is cut at `## Quotes` and `## Sources`: those hold the
family's words whole, and the sections draw them with their media. Showing
both would quote every message twice. Pure: no I/O.
"""

from __future__ import annotations

import re

from memory_ui.entry.sections import Medium, Section  # noqa: F401 (the doctests build Medium)
from memory_ui.words import words

__all__ = ["holds", "narrative"]

# The card's own sections that the entry's sections show, left out of the narrative.
_SHOWN_AS_SECTIONS = {"Quotes", "Sources"}
_HEADING = re.compile(r"^##\s+(.+?)\s*$")


def holds(sections: list[Section], *, language: str) -> str:
    """What an entry holds, for its facts line: each kind once, with its
    count, in the order the sections show them.

        >>> s = [Section("voice"), Section("images", (Medium("a.png", "image"),) * 7)]
        >>> holds(s, language="en"), holds(s, language="de")
        ('Voice and 7 photos', 'Sprachnachricht und 7 Fotos')
        >>> holds([Section("video"), Section("file"), Section("added")], language="en")
        'Video, a file and a reply'
        >>> holds([], language="en")
        ''
    """
    w = words(language)
    counts: dict[str, int] = {}
    for section in sections:
        what = _COUNTED.get(section.kind, section.kind)
        counts[what] = counts.get(what, 0) + _count(section)
    items = [_holding(what, n, w, first=i == 0) for i, (what, n) in enumerate(counts.items())]
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} {w['and']} {items[-1]}"


def narrative(text: str) -> str:
    """The card's text without what the sections show: its quotes and its
    sources. What is left is famstack's narrative (the meta line, the
    summary) and any section a person added.

        >>> narrative("> [!summary]\\n> A day at the lake.\\n\\n## Sources\\n\\n"
        ...           "> ![[/media/2026/08/a.png]]\\n> At the lake.\\n")
        '> [!summary]\\n> A day at the lake.'
    """
    kept: list[str] = []
    for heading, lines in _split(text):
        if heading in _SHOWN_AS_SECTIONS:
            continue
        if heading:
            kept.append(f"## {heading}")
        kept += lines
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


# ── Reading the text ───────────────────────────────────────────────────────

def _split(text: str) -> list[tuple[str, list[str]]]:
    """The text as (heading, lines) by its `## ` headings; the part before
    the first heading has the heading ""."""
    parts: list[tuple[str, list[str]]] = [("", [])]
    for line in text.split("\n"):
        if found := _HEADING.match(line):
            parts.append((found[1], []))
        else:
            parts[-1][1].append(line)
    return parts


# ── What an entry holds ────────────────────────────────────────────────────

# A section counts under the word the facts line uses for it.
_COUNTED = {"image": "photo", "images": "photo", "added": "reply"}


def _count(section: Section) -> int:
    """How many of its kind a section adds: a photo section its photos (one
    when the card names no file), any other section one."""
    return max(len(section.media), 1) if section.kind in ("image", "images") else 1


def _holding(what: str, n: int, w: dict, *, first: bool) -> str:
    """One item of the facts line: "Voice" or "7 photos" first, "a file"
    after it."""
    one, many = w["holds"][what]
    if n > 1:
        text = many.format(n=n)
    elif first:
        text = w["medium"].get("image" if what == "photo" else what) or one
    else:
        text = one
    return text[:1].upper() + text[1:] if first else text

