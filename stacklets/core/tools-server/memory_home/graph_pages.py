"""The memory's graph pages: one person, one topic, one sender, the documents, one kind, the senders.

Each page is the memory's overview of what its address names, for the
device's audience, drawn by its page module:

    /memory/person/<id>          open_person    person_page.sheet
    /memory/topic/<card id>      open_topic     topic_page.sheet
    /memory/sender/<card id>     open_sender    sender_page.sheet
    /memory/documents[/<kind>]   open_documents documents_page.sheet
    /memory/kind/<type>          open_kind      kind_page.sheet
    ...?part=2                   the part of the page's long list of cards
    /memory/senders              open_senders   senders_page.sheet

    answer = await open_topic("topic-camping", reads, looking, language="en",
                              never_set_up=never_set_up)
    answer.status, answer.html

Every graph page reads its own view and the menu (`menu.menu_of`): the
person's topics from their "My memory", which also gives a topic's page
how many of its cards are about the person looking, and every kind of
card with its count. A 404 from the
memory is the "not found" page (`notices.not_found`), the same for a page
that does not exist and one the device may not read, and for a part
outside the list. `/memory/kind/document` sends the browser on to the
documents page, which shows the documents by kind.

The I/O edge of these pages; the pages themselves are pure.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from memory_home import addresses, documents_page, kind_page, person_page, sender_page, senders_page, topic_page
from memory_home.frame import document
from memory_home.items import Context
from memory_home.looking import Looking, device_person
from memory_home.menu import menu_of
from memory_home.notices import NeverSetUp, no_answer, not_found
from memory_home.topic import states as topic_states
from memory_home.words import words
from memory_ui.pages import Answer
from memory_ui.reads import Reads

__all__ = ["open_person", "open_topic", "open_sender", "open_documents", "open_kind", "open_senders"]

# A graph page's drawing: the overview's answer, the context and the
# person's own topics in, (the tab's title, the sheet) out.
Draw = Callable[[dict, Context, list[dict]], tuple[str, str]]


async def open_person(person_id: str, reads: Reads, looking: Looking, *, language: str,
                      never_set_up: NeverSetUp, part: int = 1) -> Answer:
    """`/memory/person/<id>`: what the device may read about one person,
    part `part` of the cards about them."""
    return await _open(f"person/{person_id}", addresses.person(person_id),
                       lambda answer, context, mine: person_page.sheet(answer, context),
                       reads, looking, language=language, never_set_up=never_set_up, part=part)


async def open_topic(topic_id: str, reads: Reads, looking: Looking, *, language: str,
                     never_set_up: NeverSetUp, part: int = 1) -> Answer:
    """`/memory/topic/<card id>`: one topic's cards (part `part`), people
    and senders, with how many are about the person looking."""
    def draw(answer: dict, context: Context, mine: list[dict]) -> tuple[str, str]:
        about = (next((t.get("about", 0) for t in mine if t.get("id") == topic_id), 0)
                 if context.looker else None)
        return topic_page.sheet(answer, context, about=about)

    return await _open(f"topic/{topic_id}", addresses.topic(topic_id), draw,
                       reads, looking, language=language, never_set_up=never_set_up, part=part,
                       refused=topic_states.not_yours)


async def open_sender(sender_id: str, reads: Reads, looking: Looking, *, language: str,
                      never_set_up: NeverSetUp, part: int = 1) -> Answer:
    """`/memory/sender/<card id>`: the documents from one sender, part `part`."""
    return await _open(f"sender/{sender_id}", addresses.sender(sender_id),
                       lambda answer, context, mine: sender_page.sheet(answer, context),
                       reads, looking, language=language, never_set_up=never_set_up, part=part)


async def open_documents(kind: str, reads: Reads, looking: Looking, *, language: str,
                         never_set_up: NeverSetUp, part: int = 1) -> Answer:
    """`/memory/documents[/<kind>]`: the documents by kind, of `kind` or
    all (""), part `part` of their list."""
    view = f"documents/{kind}" if kind else "documents"
    return await _open(view, addresses.documents(kind),
                       lambda answer, context, mine: documents_page.sheet(answer, context, kind=kind),
                       reads, looking, language=language, never_set_up=never_set_up, part=part)


async def open_kind(card_type: str, reads: Reads, looking: Looking, *, language: str,
                    never_set_up: NeverSetUp, part: int = 1) -> Answer:
    """`/memory/kind/<type>`: every card of one kind the device may read,
    newest first, part `part` of them; "not found" for a kind it has no
    card of. The kind "document" goes on to the documents page, the same
    part: one page lists the documents, so the two never disagree."""
    if card_type == "document":
        return Answer(302, location=addresses.in_part(addresses.documents(), part))
    return await _open(f"kind/{card_type}", addresses.kind(card_type),
                       lambda answer, context, mine: kind_page.sheet(answer, context, card_type=card_type),
                       reads, looking, language=language, never_set_up=never_set_up, part=part)


async def open_senders(reads: Reads, looking: Looking, *, language: str,
                       never_set_up: NeverSetUp) -> Answer:
    """`/memory/senders`: every sender with how many documents it sent."""
    return await _open("senders", addresses.senders(),
                       lambda answer, context, mine: senders_page.sheet(answer, context),
                       reads, looking, language=language, never_set_up=never_set_up)


# ── Parts ────────────────────────────────────────────────────────────────

async def _open(view: str, here: str, draw: Draw, reads: Reads, looking: Looking, *, language: str,
                never_set_up: NeverSetUp, part: int = 1,
                refused: Callable[[str], str] | None = None) -> Answer:
    """A graph page: the memory's overview `view` for the device's
    audience, part `part` of its list, drawn by `draw`, at the address
    `here`, the first part's, which the menu marks on every part. A 404
    from the memory is the "not found" page, or the page `refused` draws
    where a kind of page has its own (a topic: the same for a hidden one
    and a missing one); no answer is the page that
    says so."""
    (status, answer), names, menu = await asyncio.gather(reads.overview(view, part), reads.names(),
                                                         menu_of(reads, looking))
    person = device_person(looking, names)
    if status == 404 and refused:
        return Answer(404, document(refused(language), title=words(language)["topic_refused"],
                                    language=language, here=here, person=person, menu=menu))
    if status == 404:
        return not_found(language=language, here=here, person=person, menu=menu)
    if answer is None:
        return await no_answer(looking, language=language, never_set_up=never_set_up)
    me = (answer.get("person") or {}).get("id", "")
    context = Context.of(answer, names, language=language, me=me, looker=person[0] if person else "")
    title, sheet = draw(answer, context, menu.topics)
    return Answer(200, document(sheet, title=title, language=language, here=here, person=person,
                                menu=menu))
