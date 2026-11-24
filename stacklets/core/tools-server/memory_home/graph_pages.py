"""The memory's graph pages: one person, one topic, one sender, the documents, one kind, the senders.

Each page is the memory's overview of what its address names, for the
device's audience, drawn by its page module:

    /memory/person/<id>          open_person    person_page.sheet
    /memory/topic/<card id>      open_topic     topic_page.sheet
    /memory/sender/<card id>     open_sender    sender_page.sheet
    /memory/documents[/<kind>]   open_documents documents_page.sheet
    /memory/kind/<type>          open_kind      kind_page.sheet
    /memory/kind/<type>/<id>     open_kind      the same, narrowed to one person's cards
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
outside the list. A topic has its own: the same refusal for a hidden topic
and a missing one, and "no more entries" for a part past the end of one the
device reads. `/memory/kind/document` sends the browser on to the
documents page, which shows the documents by kind.

The I/O edge of these pages; the pages themselves are pure.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from memory_home import addresses, documents_page, kind_page, person_page, sender_page, senders_page, topic_page
from memory_home.frame import document
from memory_home.items import Context
from memory_home.looking import Looking, device_person
from memory_home.menu import menu_of
from memory_home.notices import NeverSetUp, no_answer, not_found
from memory_home.topic import entries as topic_entries
from memory_home.topic import states as topic_states
from memory_ui import ai_route
from memory_ui.ai_route import AiPlan
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
                     never_set_up: NeverSetUp, part: int = 1,
                     ai_plan: AiPlan | None = None) -> Answer:
    """`/memory/topic/<card id>`: one topic's cards (part `part`), people
    and senders, with how many are about the person looking. `ai_plan`
    reads `stack ai recommended show` once for the page, for the AI ✦
    names, beside the memory's reads and for at most
    `ai_route.READ_SECS`; no plan, or no answer in time, names just an AI."""
    kept: dict = {}
    route = {"of": ""}

    async def read_route() -> None:
        route["of"] = await ai_route.read(ai_plan)

    def draw(answer: dict, context: Context, mine: list[dict]) -> tuple[str, str]:
        about = (next((t.get("about", 0) for t in mine if t.get("id") == topic_id), 0)
                 if context.looker else None)
        return topic_page.sheet(answer, context, about=about, media=kept, route=route["of"])

    def refused(language: str, past_end: bool) -> tuple[str, str]:
        return topic_states.no_more(topic_id, language) if past_end else topic_states.not_yours(language)

    return await _open(f"topic/{topic_id}", addresses.topic(topic_id), draw,
                       reads, looking, language=language, never_set_up=never_set_up, part=part,
                       records=(topic_entries.media_paths, kept), refused=refused, assets=topic_page.ASSETS,
                       beside=read_route())


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
                    never_set_up: NeverSetUp, part: int = 1, person: str = "") -> Answer:
    """`/memory/kind/<type>`: every card of one kind the device may read,
    newest first, part `part` of them; "not found" for a kind it has no
    card of. `/memory/kind/<type>/<person>` (`person`) narrows it to the
    cards of that kind the person's page counts, which its type chip
    opens. The kind "document" goes on to the documents page, the same
    part: one page lists the documents, so the two never disagree. A
    person's documents stay here, as their other kinds do."""
    if card_type == "document" and not person:
        return Answer(302, location=addresses.in_part(addresses.documents(), part))
    view = f"kind/{card_type}/{person}" if person else f"kind/{card_type}"
    return await _open(view, addresses.kind(card_type, person=person),
                       lambda answer, context, mine: kind_page.sheet(answer, context, card_type=card_type,
                                                                     person=person),
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
                records: tuple[Callable[[dict], list[str]], dict] | None = None,
                refused: Callable[[str, bool], tuple[str, str]] | None = None,
                assets: tuple[str, ...] = (), beside: Awaitable[None] | None = None) -> Answer:
    """A graph page: the memory's overview `view` for the device's
    audience, part `part` of its list, drawn by `draw`, at the address
    `here`, the first part's, which the menu marks on every part. A 404
    from the memory is the "not found" page, or the (title, sheet) `refused`
    draws where a kind of page has its own (a topic: the same for a hidden
    one and a missing one), told whether the page is a part past the end of
    one the device reads; no answer is the page that says so. `records` is
    for a page that draws the media store's files: the paths its answer
    names, and the dict their records go into before `draw` runs. `assets`
    are the page's own styles and scripts (`frame.document`). `beside` is a
    page's own read, run with the memory's reads and done before `draw`
    (a topic's AI route)."""
    (status, answer), names, menu, _ = await asyncio.gather(reads.overview(view, part), reads.names(),
                                                            menu_of(reads, looking), beside or _done())
    person = device_person(looking, names)
    if status == 404 and refused:
        # SAFETY: "past the end" only when part 1 answers for this same
        # audience, so a hidden page and a missing one still draw alike.
        past_end = part > 1 and (await reads.overview(view, 1))[0] == 200
        title, sheet = refused(language, past_end)
        return Answer(404, document(sheet, title=title, language=language, here=here, person=person,
                                    menu=menu, assets=assets))
    if status == 404:
        return not_found(language=language, here=here, person=person, menu=menu)
    if answer is None:
        return await no_answer(looking, language=language, never_set_up=never_set_up)
    me = (answer.get("person") or {}).get("id", "")
    context = Context.of(answer, names, language=language, me=me, looker=person[0] if person else "")
    if records:
        paths = records[0](answer)
        records[1].update(zip(paths, await asyncio.gather(*(reads.kept(p) for p in paths))))
    title, sheet = draw(answer, context, menu.topics)
    return Answer(200, document(sheet, title=title, language=language, here=here, person=person,
                                menu=menu, assets=assets))


async def _done() -> None:
    """Nothing to read, as an awaitable, for a page with no read of its own."""
