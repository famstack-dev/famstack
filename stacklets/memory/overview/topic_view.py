"""The answer for one topic: everything the topic's page draws, in one reply.

    topic_view(topic, cards, today, part=1)
    # {"bookmarks": {"count": 12, "items": [{"id": ..., "site": "example.org", "words": "..."}]},
    #  "documents": {"kinds": [...], "total": 3, ...},
    #  "lists": [{"id": "list-packing", "open": 7, "items": [the first 5 open]}],
    #  "changed": {"count": 1, "items": [...]},
    #  "entries": [the newest 10 notes in full]}      # [] on any part but the first

`views._topic` calls it with the topic's cards and merges the parts into
the page's answer, next to the topic's own `cards`, `links` and `counts`.
The topic page then draws and never asks twice.

The parts come from the overview's own section builders (`sections.py`),
so a topic's "What changed" reads the same rule as the person page: a bot's
change does not count, and a filing the archivist committed with a person
as author is that person's. This module only picks what a topic shows and
adds the two parts no other page has: a bookmark's site and words, and a
note in full (an entry).

The entries are the newest notes, which the first part of the page shows
in full; a later part lists older notes as its cards, so it sends none.
An entry's `made` is the sidecar's record of the model that wrote its text,
as the card page reads it: core decides ✦ from it with one rule
(`memory_ui.entry.made.ai_made`), so the two pages never disagree.

A list shows its open items only when it is tagged with the topic, as the
topic's to-do list: a long list breaks a page (`open_lists` sends none on
purpose), so a list that merely sits in the topic comes with its name and
open count.
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse

from stack.cards.sources import SourcesMismatch
from stack.card_render import split_body

from ..graph import Card
from ..note_card import card_sources
from .items import item
from .sections import ListCard, changed, documents, lists_of, open_lists

__all__ = ["ENTRIES", "OPEN_ITEMS", "BOOKMARKS", "DOCUMENTS", "topic_view", "entry", "site_of", "words_of"]

# How many notes come in full, and how many open items a to-do list names.
# Older notes stay in the paged `cards`. A topic names its newest bookmarks
# and documents, as many as a part of a page lists.
ENTRIES = 10
OPEN_ITEMS = 5
BOOKMARKS = 40
DOCUMENTS = 40

_SOURCES_HEADING = re.compile(r"^## Sources\s*$", re.MULTILINE)


def topic_view(topic: Card, cards: list[Card], today: date, *, part: int = 1) -> dict:
    """The parts of a topic's page from its `cards` (newest first): `bookmarks`,
    `documents`, `lists`, `changed` and `entries`. Every part is empty for a
    topic with no card of its kind; `entries` also on any `part` but the first."""
    found = lists_of(cards)
    notes = [c for c in cards if c.type == "note"][:ENTRIES] if part == 1 else []
    return {"bookmarks": _bookmarks([c for c in cards if c.type == "bookmark"]),
            "documents": documents(cards, limit=DOCUMENTS),
            "lists": _lists(topic, found),
            "changed": changed(cards, today),
            "entries": [entry(c) for c in notes]}


# ── Bookmarks ─────────────────────────────────────────────────────────────

def _bookmarks(cards: list[Card]) -> dict:
    """The bookmarks, newest first, each with its `site` and the `words` sent with it."""
    return {"count": len(cards),
            "items": [item(c, site=site_of(c), words=words_of(c)) for c in cards[:BOOKMARKS]]}


def site_of(card: Card) -> str:
    """The address's host without `www.` (`example.org`), "" for an address with none."""
    return (urlparse(str(card.meta.get("resource") or "")).hostname or "").removeprefix("www.")


def words_of(card: Card) -> str:
    """What was sent with a bookmark: its text without the `>` facts lines."""
    lines = _text(card).splitlines()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line for line in lines if not line.startswith(">"))).strip()


# ── Lists ─────────────────────────────────────────────────────────────────

def _lists(topic: Card, found: list[ListCard]) -> list[dict]:
    """Each list with open items and its open count; a list tagged with the
    topic adds its first `OPEN_ITEMS` open items."""
    todo = {lst.card.id for lst in found if topic.id in lst.card.tag_topics.values()}
    by_id = {lst.card.id: lst for lst in found}
    lines = open_lists(found)
    for line in lines:
        if line["id"] in todo:
            line["items"] = [{"title": i.text, "day": i.due or ""}
                             for i in by_id[line["id"]].parsed.open_items[:OPEN_ITEMS]]
    return lines


# ── Entries ───────────────────────────────────────────────────────────────

def entry(card: Card) -> dict:
    """A note in full: its `text` (without the title and the sources), `title`,
    `day`, `sender` (who filed it), `sources` with their quotes, and `made`, the
    sidecar's record of the model that wrote the text as it stands
    (`{"at": ..., "model": ..., "prompt_template": ...}`, `{}` for none)."""
    side = card.sidecar or {}
    return item(card, text=_text(card), sender=str(side.get("filed_by") or card.meta.get("filed_by") or ""),
                sources=_sources(card), made=dict(side.get("made") or {}))


def _sources(card: Card) -> list[dict]:
    """The note's sources with their quotes; none for a note whose quotes and sources do not pair up."""
    try:
        return [s.shown() for s in card_sources(card.text, card.sidecar or {})]
    except SourcesMismatch:
        return []


def _text(card: Card) -> str:
    """The note's body without its title heading and its Sources section."""
    body = _SOURCES_HEADING.split(_body(card))[0]
    return re.sub(r"\A\s*# .*\n", "", body.lstrip()).strip()


def _body(card: Card) -> str:
    return split_body(card.text)
