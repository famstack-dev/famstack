"""The card pages as answers: one entry per page, from the address to
a status and HTML, or a redirect.

Each entry reads what its page needs from the memory (`reads.Reads`),
decides where the address lands and which page answers (the card, a
notice, the "not set up" page), and renders it. The web server only
routes: it passes the language, the time, the memory's reads and the one
question only the host can answer, whether the memory was ever set up,
and turns the answer into a response.

    reads = Reads("http://stack-memory-curator:8090")
    answer = await open_card("note-0b6e2d91aa", reads, now=now, language="en",
                             paperless="http://192.168.1.20:42020",
                             never_set_up=memory_never_set_up)
    answer      Answer(status=200, html="<!doctype html>...", location="")

What each entry answers:

    open_card     200 the card; 302 to its id; 410 who removed it, when;
                  404 no such card; 200 "does not answer"; 404 "not set up"
    open_history  200 the card's history; 404 "no history"
    open_text     200 a note's full text; 404 "no text"
    no_card       404 "not set up", or 404 "no card has this address"

Why the pages answer here and not in the server: what a page reads,
which notice it shows and which files it fetches first are the page's
own decisions. The next card type that shows a stored file changes its
module in this package, not the web server.

Where an address lands, from the memory's answer to `card/<ref>`
(`landing`):

    /card/note-0b6e2d91aa         the card has this id        200, the page
    /card/bookmark-1a2b3c4d5e     a former id of it           302 to /card/note-0b6e2d91aa
    /card/homer/notes/grill.md    the card's file             302 to /card/note-0b6e2d91aa
    /card/note-ffffffffff         no card has it              404, the page
    /card/note-0b6e2d91aa         the memory does not answer  200, a page that says so

Why the server asks the memory before it sends the page: a status is what
a browser, a link checker and a test read, and only the server can give
one. A former id or a file path is sent on to the card's current id, so a
card has one address and a bookmark made today still opens next year.

`never_set_up` is asked only when the memory did not answer, because it
runs every health check of the stack (`stack list`) and takes seconds.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from html import escape

from memory_ui import card_page, history, notices
from memory_ui.addresses import card_address
from memory_ui.entry import media_paths
from memory_ui.file_notes import file_of
from memory_ui.reads import Reads
from memory_ui.words import words
from stack.cards.lookup import Removal

__all__ = ["Answer", "Landing", "landing", "open_card", "open_history", "open_text", "no_card"]

# Whether the memory was ever set up on this instance: asked of the host,
# true only when the stack answers and lists no memory stacklet.
NeverSetUp = Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class Answer:
    """A page's answer: its status, and its HTML or the address to go on to.

        Answer(200, "<!doctype html>...")                  the page
        Answer(302, location="/card/note-0b6e2d91aa")    go on to the card
        Answer(410, "<!doctype html>...")                  the card was removed
    """

    status: int
    html: str = ""
    location: str = ""


async def open_card(ref: str, reads: Reads, *, now: datetime, language: str, paperless: str,
                    never_set_up: NeverSetUp) -> Answer:
    """The page of the card `ref` names (`/card/<ref>`).

    This page is the one place that decides which card a card id names,
    for `/card/<ref>` and for `/go/card/<card id>`, which comes straight
    here. The memory says whether `ref` names a card
    (`landing`): a former id or a file path goes on to the
    card's id with 302. When no card has it, the memory's history says
    whether one had it: who removed it and on which day, with 410, so a
    link checker knows it is gone for good. Else a notice with 404. No
    answer is a notice that the memory does not answer (or the "not set
    up" page with 404). A card's page reads the people's names and the
    media store's records of the files it shows, all at once.

        await open_card("bookmark-1a2b3c4d5e", reads, ...)
        Answer(status=302, html='', location='/card/note-0b6e2d91aa')
        await open_card("note-6e5d4c3b2a", reads, ...)
        Answer(status=410, html='...Marge removed it on 3 November...', location='')
    """
    status, card = await reads.card(ref)
    where = landing(ref, status, (card or {}).get("id"))
    if where.location:
        return Answer(where.status, location=where.location)
    if card is None:
        if where.status == 404 and (removal := await reads.removal(ref)):
            return await _gone(removal, reads, today=now.date(), language=language)
        if where.status != 404 and await never_set_up():
            return Answer(404, notices.not_set_up(language=language))
        said = words(language)["missing" if where.status == 404 else "unreachable"]
        return Answer(where.status, card_page.notice(said, language=language))
    found, paths = file_of(card), media_paths(card)
    names, kept, *records = await asyncio.gather(
        reads.names(), reads.kept(found.path) if found else _nothing(),
        *(reads.kept(p) for p in paths))
    return Answer(200, card_page.page(card, names=names, now=now, language=language, kept=kept,
                                      paperless=paperless, media=dict(zip(paths, records))))


async def open_history(card_id: str, reads: Reads, *, now: datetime, language: str) -> Answer:
    """A card's history as the fragment "History" loads
    (`/card/<card id>/history`); 404 with a line that says there is none.

        await open_history("note-0b6e2d91aa", reads, now=now, language="en")
        Answer(200, '<ol class="timeline">...')
    """
    versions, names = await asyncio.gather(reads.versions(card_id), reads.names())
    if versions is None:
        return Answer(404, _empty(words(language)["no_history"]))
    return Answer(200, history.fragment(versions, names=names, now=now, language=language))


async def open_text(card_id: str, reads: Reads, *, language: str) -> Answer:
    """A note's full text as the fragment "Full text" loads
    (`/card/<card id>/text`); 404 with a line that says there is none.

        await open_text("note-4e1a9b7c2d", reads, language="en")
        Answer(200, '<p>UNO. Rules of the game...')
    """
    _, card = await reads.card(card_id)
    text = card_page.full_text(card, language=language) if card else None
    if text is None:
        return Answer(404, _empty(words(language)["no_text"]))
    return Answer(200, text)


async def no_card(*, language: str, never_set_up: NeverSetUp) -> Answer:
    """The answer to a card link no card answers to: the "not set up" page,
    or the notice that no card has the address. Both are a 404.

        await no_card(language="en", never_set_up=...)   Answer(404, "...No card has this address...")
    """
    if await never_set_up():
        return Answer(404, notices.not_set_up(language=language))
    return Answer(404, card_page.notice(words(language)["missing"], language=language))


# ── Where an address lands ─────────────────────────────────────────────────

@dataclass(frozen=True)
class Landing:
    """Where a request for a card's address lands: a status, and the
    address to go on to when the status is a redirect.

        Landing(200)                              the page, this card open
        Landing(302, "/card/note-0b6e2d91aa")   go on to the card's address
        Landing(404)                              the page, and no card has the address
    """

    status: int
    location: str = ""


def landing(ref: str, status: int | None, card_id: str | None) -> Landing:
    """Where `/card/<ref>` lands, given the memory's answer to `card/<ref>`.

    `status` is the HTTP status the cards API answered with, None when it
    did not answer at all; `card_id` is the id of the card it sent. A card
    whose id is `ref` lands on the page. A card found by another name (a
    former id, its file's path) lands on its own address. An answer of 404
    lands on the page with 404. No answer, or a server error, lands on the
    page with 200, because the page then says that the memory does not
    answer.

        >>> landing("note-0b6e2d91aa", 200, "note-0b6e2d91aa")
        Landing(status=200, location='')
        >>> landing("bookmark-1a2b3c4d5e", 200, "note-0b6e2d91aa")
        Landing(status=302, location='/card/note-0b6e2d91aa')
        >>> landing("note-ffffffffff", 404, None)
        Landing(status=404, location='')
        >>> landing("note-0b6e2d91aa", None, None)
        Landing(status=200, location='')
    """
    if status is None or status >= 500:
        return Landing(200)
    if status != 200 or not card_id:
        return Landing(404)
    if card_id != ref:
        return Landing(302, card_address(card_id))
    return Landing(200)


# ── Small parts ────────────────────────────────────────────────────────────

async def _gone(removal: Removal, reads: Reads, *, today: date, language: str) -> Answer:
    """The page of a removed card: who removed it (a person's id, shown by
    their name) and on which day, with 410."""
    names = await reads.names()
    return Answer(410, notices.gone(removal.who, date.fromisoformat(removal.day), names=names,
                                    today=today, language=language))


def _empty(line: str) -> str:
    """A fragment that says there is nothing to show."""
    return f'<p class="empty-text">{escape(line)}</p>'


async def _nothing() -> None:
    """No read: the card shows no file of its own."""
    return None
