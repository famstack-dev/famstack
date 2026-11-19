"""The pages that stand in for a memory page: "not found", and no answer.

    not_found(language="en", here="/memory/person/grampa", person=("marge", "Marge"), menu=Menu())
    await no_answer(looking, language="en", never_set_up=never_set_up)

**A page the device may not read is a page that does not exist.** The
memory answers 404 for both (`/overview/<view>`), and both get the same
"not found" page, which names nothing of what was asked, so it tells
nothing about what another person keeps in their own folder.

**A memory that does not answer is not an error**: the page says so, as
the card pages do, or that the memory is not set up yet.

Pure but for `never_set_up`, which asks the host.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from html import escape

from memory_home import addresses
from memory_home.frame import HEADING_ID, document
from memory_home.looking import Looking, device_person
from memory_home.menu import Menu
from memory_home.words import words
from memory_ui import notices
from memory_ui.pages import Answer

__all__ = ["NeverSetUp", "not_found", "no_answer"]

NeverSetUp = Callable[[], Awaitable[bool]]


def not_found(*, language: str, here: str, person: tuple[str, str] | None, menu: Menu) -> Answer:
    """The memory's "not found" page, with 404, in the frame of the device's
    person and its menu. It names nothing of what was asked: a page the
    device may not read and one that does not exist must look the same."""
    w = words(language)
    sheet = (f'<article class="sheet notice"><h1 class="title" id="{HEADING_ID}" tabindex="-1">'
             f'{escape(w["not_found"])}</h1><p class="intro">{escape(w["not_found_line"])}</p>'
             f'<p class="actions"><a class="btn primary" href="{addresses.home()}">'
             f'{escape(w["my_memory"])}</a></p></article>')
    return Answer(404, document(sheet, title=w["not_found"], language=language, here=here,
                                person=person, menu=menu))


async def no_answer(looking: Looking, *, language: str, never_set_up: NeverSetUp) -> Answer:
    """The memory did not answer: the page that says it is not set up (404),
    or that it does not answer (200), as the card pages say both, each with
    the memory's header and no menu groups."""
    person = device_person(looking, {})

    def frame(sheet: str, *, title: str, description: str = "") -> str:
        return document(sheet, title=title, language=language, here=addresses.home(), person=person)

    if await never_set_up():
        return Answer(404, notices.not_set_up(language=language, frame=frame))
    said = words(language)["card"]["unreachable"]
    sheet = f'<article class="sheet card"><p class="notice">{escape(said)}</p></article>'
    return Answer(200, frame(sheet, title=words(language)["my_memory"]))
