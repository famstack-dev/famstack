"""A kind's page: every card of one kind the device may read, newest first.

The menu's "Everything" opens `/memory/kind/<type>` for each kind of card
but the documents (`/memory/kind/note`, `/memory/kind/diary`), drawn from
the memory's overview of that kind (`/overview/kind/<type>`):

    title, sheet = kind_page.sheet(answer, context, card_type="note")
    title, sheet = kind_page.sheet(answer, context, card_type="note", person="marge")

A type chip on My memory or a person's page opens the same page narrowed
to that person (`/memory/kind/note/marge`): the cards of the kind the
chip counted, with the person's name beside the count, linked to their page.

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
from memory_home.items import Context, person_name
from memory_home.paging import pager
from memory_home.words import counted, words

__all__ = ["sheet"]


def sheet(answer: dict, context: Context, *, card_type: str, person: str = "") -> tuple[str, str]:
    """(the browser tab's title, the page's sheet) for the overview
    `answer` of the kind `card_type` ("note"), narrowed to the cards of
    `person` when one is named. The title is the kind's name as the menu
    says it ("Notes").

        sheet({"cards": {"count": 2, "items": [...]}, "links": {...}}, context, card_type="note")
        # ("Notes", '<article class="sheet home">... 2 cards ...')
    """
    w = words(context.language)
    title = w["types"].get(card_type, card_type)
    cards = answer.get("cards") or {}
    whose = (f' · <a class="whose" href="{escape(_page_of(person, context))}">'
             f'{escape(person_name(person, context))}</a>' if person else "")
    head = page_head(card_type, w["everything"], title,
                     f'<span class="count-line">{escape(counted(cards.get("count", 0), w["cards"]))}</span>'
                     + whose)
    body = (sections.cards(answer, context) + pager(cards, addresses.kind(card_type, person=person), context)
            + sections.links(answer, context))
    return title, f'<article class="sheet home">{head}<div class="sections">{body}</div></article>'


def _page_of(person: str, context: Context) -> str:
    """The page the person's name leads back to: My memory for the person
    looking, else their person page."""
    return addresses.home() if person == context.looker else addresses.person(person)
