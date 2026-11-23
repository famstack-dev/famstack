"""A card's page in the memory's frame: the header, the search and the menu around the card.

The web server sends `/card/<ref>`, and a card link no card answers to,
here:

    looking = await card_looking(request)
    answer = await open_card("note-0b6e2d91aa", reads, looking, now=now, language="en",
                             paperless="http://192.168.1.20:42020", never_set_up=never_set_up)
    answer = await no_card(reads, looking, language="en", never_set_up=never_set_up)

**A card is one step in the memory's graph.** Its page keeps the
memory's header (the mark, the one search, who looks) and the menu the
device's `/memory` shows, so a walk through the memory never ends on a
card. The card viewer (`memory_ui.pages`) still decides which card an
address names and draws the card; this module gives it the frame to ask
for (`memory_ui.frame.Framing`). The card pages never import this package.

**The frame is read only for a page.** The card viewer asks for it when
it draws one, so a redirect (a former id, a file's path) reads nothing for
it. The people's names are read once for the frame and the card together
(`Reads.names`).

**Every page in place of a card has the same frame**: a card the device
may not read, one that does not exist, a removed one, a memory that does
not answer or was never set up. The frame is read for the device, never
for the card asked, so the page of a card the device may not read and
the page of one that does not exist stay the same, byte for byte.

No link of the menu leads to a card's page, so none is marked current.

The I/O edge of the card pages' frame: who looks, the menu and the
people's names, read for the device; `frame.py` draws it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import Request

import devices
from memory_home.frame import document
from memory_home.looking import Looking, device_person
from memory_home.menu import menu_of
from memory_home.notices import NeverSetUp
from memory_ui import pages
from memory_ui.ai_route import AiPlan
from memory_ui.frame import Frame, Framing
from memory_ui.pages import Answer
from memory_ui.reads import Reads

__all__ = ["card_looking", "open_card", "no_card"]


async def card_looking(request: Request) -> Looking:
    """Who looks at a card's page, as the gate found them. The family is
    asked of the host only for the Mac's address, whose header and topics
    are the admin's (`looking.py`); a person's phone names its person, and
    a household device's frame names no one, so neither asks the host."""
    looking = devices.who(request)
    family = await devices.host.family() if looking.kind == "mac" else None
    return Looking(looking.kind, looking.person, family)


async def open_card(ref: str, reads: Reads, looking: Looking, *, now: datetime, language: str,
                    paperless: str, never_set_up: NeverSetUp, ai_plan: AiPlan | None = None) -> Answer:
    """`/card/<ref>`: the card viewer's answer (`memory_ui.pages.open_card`)
    in the memory's frame for the device `looking`, ✦ named by the route
    `ai_plan` reads. A redirect stays a
    redirect and reads no frame; every page, a notice included, has it."""
    return await pages.open_card(ref, reads, now=now, language=language, paperless=paperless,
                                 never_set_up=never_set_up, ai_plan=ai_plan,
                                 framing=_framing(reads, looking, language=language))


async def no_card(reads: Reads, looking: Looking, *, language: str, never_set_up: NeverSetUp) -> Answer:
    """A card link no card answers to (`/card` alone, `/go/card/<id>` that
    names nothing the device may read): the card viewer's 404 page in the
    memory's frame for the device `looking`."""
    return await pages.no_card(language=language, never_set_up=never_set_up,
                               framing=_framing(reads, looking, language=language))


def _framing(reads: Reads, looking: Looking, *, language: str) -> Framing:
    """What reads the memory's frame for the device `looking` when the card
    viewer asks: its person and the menu its `/memory` shows, both read
    for the device's audience. A memory that does not answer gives a menu
    with no groups; the header stays."""
    async def framing() -> Frame:
        menu, names = await asyncio.gather(menu_of(reads, looking), reads.names())
        person = device_person(looking, names)

        def frame(sheet: str, *, title: str, description: str = "") -> str:
            return document(sheet, title=title, language=language, here="", person=person, menu=menu,
                            description=description)
        return frame
    return framing
