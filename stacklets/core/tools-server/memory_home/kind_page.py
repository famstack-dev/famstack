"""A kind's page: every card of one kind the device may read, newest first.

The menu's "Everything" opens `/memory/kind/<type>` for each kind of card
but the documents (`/memory/kind/note`, `/memory/kind/diary`), drawn from
the memory's overview of that kind (`/overview/kind/<type>`):

    title, sheet = kind_page.sheet(answer, context, card_type="note")

The page names the kind under "Everything", says how many cards of it the
device may read, the count the menu shows, then lists them newest first,
each a row to its card's page, 40 at a time with "Newer" and "Older"
between the parts (`paging`), and ends with the people, topics and
senders they name. The documents keep their own page (`documents_page`),
which shows them by kind of document.

Pure: no I/O.
"""

from __future__ import annotations

from html import escape

from memory_home import addresses, sections
from memory_home.frame import page_head
from memory_home.items import Context
from memory_home.paging import pager
from memory_home.words import counted, words

__all__ = ["sheet"]


def sheet(answer: dict, context: Context, *, card_type: str) -> tuple[str, str]:
    """(the browser tab's title, the page's sheet) for the overview
    `answer` of the kind `card_type` ("note"). The title is the kind's
    name as the menu says it ("Notes").

        sheet({"cards": {"count": 2, "items": [...]}, "links": {...}}, context, card_type="note")
        # ("Notes", '<article class="sheet home">... 2 cards ...')
    """
    w = words(context.language)
    title = w["types"].get(card_type, card_type)
    cards = answer.get("cards") or {}
    head = page_head(card_type, w["everything"], title,
                     f'<span class="count-line">{escape(counted(cards.get("count", 0), w["cards"]))}</span>')
    body = (sections.cards(answer, context) + pager(cards, addresses.kind(card_type), context)
            + sections.links(answer, context))
    return title, f'<article class="sheet home">{head}<div class="sections">{body}</div></article>'
