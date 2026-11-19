"""The entries of "My memory", the family's page, the search and the preview.

The web server routes each address here and sends the answer:

    looking = Looking(kind="person", person="marge", family=None)
    answer = await open_mine(reads, looking, language="en", never_set_up=never_set_up)
    answer.status, answer.html

    /memory                      "My memory": the person on the device; the family's page
                                 on a household device; the admin's page on the Mac's address
    /memory/family               the family's page, for every device
    /memory/search               what the memory's `find` found for the header's words (`?q=`)
    /memory/search/results       the same results without the page around them, for the
                                 search's layer over the page the person is on (`?q=`)
    /memory/preview/<card id>    a card link's preview, as JSON, for the pages' script

The person, topic, sender and documents pages are `graph_pages.py`.

**Who is looking decides the view, and only that** (`looking.py`). A
phone paired as Marge asks the memory for `me`, a household device for
`family`, and the Mac's address for `me` with the admin's audience. What
each view holds is the memory's to decide: the reads carry the device's
audience (`memory_ui.Reads`, from `devices.who`), the memory answers with
only the cards that audience may see, and these pages draw the answer as
it is. No rule here or in the page modules says which card a person may
see, or which page a name opens.

**The menu lists the person's topics and every kind of card** on every
page (`menu.py`): "My memory" has its topics already and reads only the
kinds; every other page reads both.

A memory that does not answer is not an error: the page says so, as the
card pages do, or that the memory is not set up yet (`notices.py`).

The I/O edge of these pages; the pages themselves are pure
(`my_page.py`, `family_page.py`, `search_page.py`, `sections.py`).
"""

from __future__ import annotations

import asyncio
from datetime import date
from html import escape

import family_view
from memory_home import addresses, family_page, my_page, search_page
from memory_home.frame import document
from memory_home.items import Context
from memory_home.looking import Looking, device_person, family_name, mine, view_of
from memory_home.menu import Menu, everything, menu_of
from memory_home.notices import NeverSetUp, no_answer
from memory_home.words import words
from memory_ui.card_page import description_of
from memory_ui.pages import Answer
from memory_ui.reads import Reads

__all__ = ["open_mine", "open_family", "open_search", "open_search_results", "open_preview"]


async def open_mine(reads: Reads, looking: Looking, *, language: str, never_set_up: NeverSetUp) -> Answer:
    """`/memory`: the page of the person `looking` is, or the family's page
    for a device that is no person's. On the Mac's address it is the
    admin's page, read with the admin's audience; it falls back to the
    family's page when the memory has no person card for the admin."""
    here = addresses.home()
    if view_of(looking) == "family":
        return await open_family(reads, looking, language=language, never_set_up=never_set_up, here=here)
    (_, answer), names, found = await asyncio.gather(mine(reads, looking).overview("me"), reads.names(),
                                                     everything(reads))
    if answer is None:
        return await no_answer(looking, language=language, never_set_up=never_set_up)
    if looking.kind == "mac" and not (answer.get("person") or {}).get("card"):
        return await open_family(reads, looking, language=language, never_set_up=never_set_up, here=here)
    person = _person_of(answer, names, looking)
    context = Context.of(answer, names, language=language, me=person[0], looker=person[0])
    return Answer(200, document(my_page.sheet(answer, context, name=person[1]),
                                title=words(language)["my_memory"], language=language,
                                here=here, person=person, menu=Menu.of(answer.get("topics"), found)))


async def open_family(reads: Reads, looking: Looking, *, language: str, never_set_up: NeverSetUp,
                      here: str = "") -> Answer:
    """`/memory/family`: the family's page, with what the device may read.
    `here` is the address the page is shown at, `/memory/family` unless
    "My memory" shows it (`open_mine`); the menu marks only the link to it."""
    here = here or addresses.family()
    (_, answer), names, menu = await asyncio.gather(reads.overview("family"), reads.names(),
                                                    menu_of(reads, looking))
    if answer is None:
        return await no_answer(looking, language=language, never_set_up=never_set_up)
    w = words(language)
    called = family_view.called((looking.family or {}).get("name", ""), language)
    heading = w["family_of"].format(family=called) if called else w["the_family"]
    person = device_person(looking, names)
    context = Context.of(answer, names, language=language, looker=person[0] if person else "")
    return Answer(200, document(family_page.sheet(answer, context, family=heading),
                                title=w["the_family"], language=language,
                                here=here, person=person, menu=menu))


async def open_search(reads: Reads, looking: Looking, query: str, *, today: date, language: str,
                      never_set_up: NeverSetUp) -> Answer:
    """`/memory/search?q=`: what the memory's `find` found for `query`,
    for the device's audience, grouped by type; no words ask for them.
    `today` is the instance's day, which the rows' days are told from."""
    answer, names, menu = await asyncio.gather(_find(reads, query), reads.names(),
                                               menu_of(reads, looking))
    if answer is None:
        return await no_answer(looking, language=language, never_set_up=never_set_up)
    found, total = answer
    person = device_person(looking, names)
    context = Context(language=language, today=today, me=person[0] if person else "", names=names)
    return Answer(200, document(search_page.sheet(found, context, query=query, total=total),
                                title=words(language)["search_title"], language=language,
                                here=addresses.search(), person=person, menu=menu, query=query))


async def open_search_results(reads: Reads, looking: Looking, query: str, *, today: date,
                              language: str) -> Answer:
    """`/memory/search/results?q=`: the search page's sheet alone, for the
    search's layer (`static/search_layer.js`), which shows it over the page
    the person is on. The same reads, so the same results for the same
    device; its heading has the layer's id. A memory that does not answer
    is 503 with a one-line notice, which the layer shows as it is.

        Answer(200, '<article class="sheet home search">...</article>')
    """
    answer, names = await asyncio.gather(_find(reads, query), reads.names())
    if answer is None:
        said = words(language)["card"]["unreachable"]
        return Answer(503, f'<article class="sheet card"><p class="notice">{escape(said)}</p></article>')
    found, total = answer
    person = device_person(looking, names)
    context = Context(language=language, today=today, me=person[0] if person else "", names=names)
    return Answer(200, search_page.sheet(found, context, query=query, total=total,
                                         heading_id=search_page.LAYER_HEADING_ID))


async def open_preview(reads: Reads, card_id: str, *, today: date, language: str) -> tuple[int, dict]:
    """`/memory/preview/<card id>`: (status, JSON) of the preview a card
    link shows on hover or focus: the card's id, type, title and the card
    viewer's description (`description_of`). The card is read for the
    device's audience, so a card it may not read answers 404, as one that
    does not exist; a memory that does not answer is 503.

        (200, {"id": "note-1", "type": "note", "title": "Tent", "description": "Pitch 14 ..."})
    """
    # SAFETY: the preview reads the card through `reads`, which carries the
    # device's audience; it never asks the memory for a card without it.
    (status, card), names = await asyncio.gather(reads.card(card_id), reads.names())
    if status is None:
        return 503, {"detail": "the memory does not answer"}
    if card is None:
        return 404, {"detail": "Not Found"}
    return 200, {"id": card.get("id", card_id), "type": card.get("type", ""),
                 "title": card.get("title", ""),
                 "description": description_of(card, names=names, today=today, language=language)}


# ── Parts ────────────────────────────────────────────────────────────────

def _find(reads: Reads, query: str):
    """The memory's `find` for `query`, or nothing when it has no words."""
    return reads.search(query) if query.strip() else _nothing()


async def _nothing() -> tuple[list[dict], int]:
    """No search without words: the memory's `find` would answer the newest cards."""
    return [], 0


def _person_of(answer: dict, names: dict[str, str], looking: Looking) -> tuple[str, str]:
    """The page's person as (id, first name): the name on their person card,
    else the family's name for them, else their id written as a name."""
    person = answer.get("person") or {}
    person_id = person.get("id") or looking.person
    title = str(person.get("title") or "").strip()
    return person_id, (title.split()[0] if title else family_name(person_id, names, looking))
