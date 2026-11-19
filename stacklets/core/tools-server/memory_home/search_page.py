"""The search page: what the memory's `find` found for the header's words, grouped by type.

The header's one search sends its words to `/memory/search?q=`. The memory
answers them with its `find` for the device's audience (`Reads.find`), and
this module draws that answer:

    sheet(found, context, query="dentist")
    # the heading, then one group per type: people, topics, senders first,
    # then the cards' types in the order their best match came

A person, a topic and a sender link their memory page (`addresses`): the
reader searched for them, not for their card. Every other result is a row
as the memory's other pages draw a card (`items.row`), its title linking
the card's page.

`find` reads a card's whole file, its fields included, so one search finds
titles, summaries, senders, kinds of document, a bookmark's site, topics
and people's names. The page finds nothing by itself and filters nothing:
which cards the device may see is the memory's answer.

The same sheet is the search's layer (`static/search_layer.js`): the
script reads it from `/memory/search/results?q=` and shows it over the
page the person is on, with its own heading id (`LAYER_HEADING_ID`).

Pure: no I/O.
"""

from __future__ import annotations

from html import escape

from memory_home import addresses
from memory_home.frame import HEADING_ID
from memory_home.items import Context, row, type_icon
from memory_home.words import counted, words

__all__ = ["sheet", "LAYER_HEADING_ID"]

# The types that have a memory page of their own, in the order the page
# shows their groups: the reader names a person, a topic or a sender more
# often than one card.
GRAPH_TYPES = ("person", "topic", "correspondent")
# The heading of the search's layer (`static/search_layer.js`), which takes
# the focus when the layer opens. The page under the layer keeps its own
# heading (`HEADING_ID`), and one page holds each id once.
LAYER_HEADING_ID = "search-title"


def sheet(found: list[dict], context: Context, *, query: str, total: int | None = None,
          heading_id: str = HEADING_ID) -> str:
    """The search page's sheet for `query` and the cards `found` (the
    memory's `find` answer, best first). `total` is how many the memory
    found in all, when that is more than `found` holds (the answer is
    capped), and the count line then says that it shows the first ones.
    No words ask for them; words with no result say so. `heading_id` is
    the heading's id: the page's own, or `LAYER_HEADING_ID` for the layer.

        sheet([{"id": "topic-teeth", "type": "topic", "title": "Teeth"}], context, query="teeth")
    """
    w = words(context.language)
    head = (f'<header class="family-head"><h1 class="title" id="{heading_id}" tabindex="-1">'
            f'{escape(w["search_title"])}</h1>'
            + (f'<p class="facts"><span class="count-line">{escape(_count(len(found), total, w))}'
               f'</span></p>' if found else "")
            + "</header>")
    if not query.strip():
        body = f'<p class="empty-text">{escape(w["ask_words"])}</p>'
    elif not found:
        body = f'<p class="empty-text">{escape(w["no_match"].format(q=query))}</p>'
    else:
        body = "".join(_group(kind, items, context) for kind, items in grouped(found))
    return f'<article class="sheet home search">{head}<div class="sections">{body}</div></article>'


def grouped(found: list[dict]) -> list[tuple[str, list[dict]]]:
    """`found` as (type, cards) groups: the graph's types first, in
    `GRAPH_TYPES` order, then every other type in the order of its best
    match. Each group keeps the memory's order.

        >>> grouped([{"type": "note"}, {"type": "topic"}, {"type": "note"}])
        [('topic', [{'type': 'topic'}]), ('note', [{'type': 'note'}, {'type': 'note'}])]
    """
    groups: dict[str, list[dict]] = {}
    for item in found:
        groups.setdefault(str(item.get("type") or ""), []).append(item)
    order = [t for t in GRAPH_TYPES if t in groups] + [t for t in groups if t not in GRAPH_TYPES]
    return [(t, groups[t]) for t in order]


# ── Parts ────────────────────────────────────────────────────────────────

def _count(shown: int, total: int | None, w: dict) -> str:
    """"3 matches", or "The first 50 of 200 matches" when the memory found
    more than its answer holds."""
    if total is None or total <= shown:
        return counted(shown, w["found"])
    return w["found_first"].format(shown=shown, n=total)


def _group(kind: str, items: list[dict], context: Context) -> str:
    """One type's results as a section of the page, named by its type."""
    w = words(context.language)
    label = w["groups"].get(kind) or w["types"].get(kind) or kind.capitalize()
    lines = "".join(_graph_row(i) if kind in GRAPH_TYPES else row(i, context) for i in items)
    return (f'<section class="section fl-section results" data-group="{escape(kind)}" '
            f'aria-labelledby="g-{escape(kind)}"><h2 id="g-{escape(kind)}">'
            f'<span class="section-title">{escape(label)}</span>'
            f'<span class="fl-count">{len(items)}</span></h2>'
            f'<ul class="fl-rows">{lines}</ul></section>')


def _graph_row(item: dict) -> str:
    """A person, a topic or a sender as a row that links its memory page.
    The day's column stays empty, because the rows share one grid with
    the cards' rows."""
    return (f'<li class="row"><span class="fl-date"></span>{type_icon(item["type"])}'
            f'<span class="fl-main"><span class="change-line">'
            f'<a href="{escape(_page_of(item))}">{escape(item.get("title") or item["id"])}</a>'
            f'</span></span></li>')


def _page_of(item: dict) -> str:
    """The memory page of a person, topic or sender: a person's by the id
    the memory names their page by (`person_page`, `bart`), the others' by
    their card id."""
    if item["type"] == "person":
        return addresses.person(str(item.get("person_page") or item["id"]))
    if item["type"] == "topic":
        return addresses.topic(item["id"])
    return addresses.sender(item["id"])
