"""The routes of the memory's pages on core's tools server (`/memory/...`).

The web server adds them once, with what every page needs from it:

    memory_home.routes(app, reads=_reads, language=_language,
                       never_set_up=_memory_never_set_up, answer=_answer, today=lambda: _now().date())

    GET /memory                       "My memory" (`pages.open_mine`)
    GET /memory/family                the family's page
    GET /memory/person/<id>           a person's page
    GET /memory/topic/<card id>       a topic's page
    GET /memory/sender/<card id>      a sender's page
    GET /memory/documents[/<kind>]    the documents by kind
    GET /memory/kind/<type>           every card of one kind (`note`, `diary`)
    GET /memory/senders               every sender, with its documents
    GET /memory/search?q=<words>      the search (`pages.open_search`)
    GET /memory/search/results?q=     its results alone, for the search's layer
    GET /memory/preview/<card id>     a card link's preview, as JSON (`pages.open_preview`)
    GET /memory/static/<name>         the pages' own stylesheet and script

The pages are on the home address for paired devices and on the Mac's
address, as the card pages are; the device gate (`devices.gate`) runs
before any of them. Each route asks who looks, passes the reads for that
device's audience, and sends the page's answer.

The routes live here and not in `server.py` because they are the memory
pages' own edge: `server.py` only adds them, as it adds the pairing
routes (`devices.routes`), so it does not grow with every memory page.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

import devices
from access import MEMORY_PREFIX
from memory_home.graph_pages import (open_documents, open_kind, open_person, open_sender, open_senders,
                                     open_topic)
from memory_home.looking import Looking
from memory_home.pages import open_family, open_mine, open_preview, open_search, open_search_results
from memory_home.static_files import asset
from memory_ui.pages import Answer
from memory_ui.reads import Reads

__all__ = ["routes"]


def routes(app: FastAPI, *, reads: Callable[[Request], Reads], language: Callable[[Request], str],
           never_set_up: Callable[[], Awaitable[bool]], answer: Callable[[Answer], Response],
           today: Callable[[], date]) -> None:
    """Add the memory's pages to `app`. `reads` gives the memory's reads
    for a request's audience (the audience from `devices.who`, never from
    the request), `language` the page's language, `never_set_up` whether
    the memory was never set up here, `answer` turns a page's answer
    into the response, and `today` is the instance's day, which the
    search's rows and a preview's facts are told from."""

    @app.get(f"{MEMORY_PREFIX}/static/{{name}}", include_in_schema=False)
    async def memory_asset(name: str):
        found = asset(name)
        if found is None:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return Response(found[1], media_type=found[0])

    @app.get(MEMORY_PREFIX, include_in_schema=False)
    async def my_memory(request: Request):
        return answer(await open_mine(reads(request), await _looking(request),
                                      language=language(request), never_set_up=never_set_up))

    @app.get(f"{MEMORY_PREFIX}/family", include_in_schema=False)
    async def family_memory(request: Request):
        return answer(await open_family(reads(request), await _looking(request),
                                        language=language(request), never_set_up=never_set_up))

    # The graph pages: each names what it is about in its address and
    # opens it for the device looking.
    graph_pages = (("person/{ref}", open_person), ("topic/{ref}", open_topic),
                   ("sender/{ref}", open_sender), ("documents/{ref}", open_documents),
                   ("kind/{ref}", open_kind))
    for address, open_page in graph_pages:
        route = _graph_route(open_page, reads=reads, language=language, never_set_up=never_set_up,
                             answer=answer)
        app.add_api_route(f"{MEMORY_PREFIX}/{address}", route, methods=["GET"], include_in_schema=False)

    @app.get(f"{MEMORY_PREFIX}/search", include_in_schema=False)
    async def search_memory(request: Request, q: str = ""):
        return answer(await open_search(reads(request), await _looking(request), q, today=today(),
                                        language=language(request), never_set_up=never_set_up))

    @app.get(f"{MEMORY_PREFIX}/search/results", include_in_schema=False)
    async def search_results(request: Request, q: str = ""):
        return answer(await open_search_results(reads(request), await _looking(request), q, today=today(),
                                                language=language(request)))

    @app.get(f"{MEMORY_PREFIX}/preview/{{card_id}}", include_in_schema=False)
    async def card_preview(card_id: str, request: Request):
        status, preview = await open_preview(reads(request), card_id, today=today(),
                                             language=language(request))
        return JSONResponse(preview, status_code=status)

    @app.get(f"{MEMORY_PREFIX}/senders", include_in_schema=False)
    async def all_senders(request: Request):
        return answer(await open_senders(reads(request), await _looking(request),
                                         language=language(request), never_set_up=never_set_up))

    @app.get(f"{MEMORY_PREFIX}/documents", include_in_schema=False)
    async def all_documents(request: Request):
        return answer(await open_documents("", reads(request), await _looking(request),
                                           language=language(request), never_set_up=never_set_up))


def _graph_route(open_page, *, reads, language, never_set_up, answer):
    """The route of one graph page: `open_page` for the address's `ref`,
    with what `routes` was given."""
    async def route(ref: str, request: Request):
        return answer(await open_page(ref, reads(request), await _looking(request),
                                      language=language(request), never_set_up=never_set_up))
    return route


async def _looking(request: Request) -> Looking:
    """Who looks at a memory page, as the gate found them; the family (its
    name, its admin) is asked of the host only for a device that is no
    person's, which is when a page needs it."""
    looking = devices.who(request)
    family = None if looking.kind == "person" else await devices.host.family()
    return Looking(looking.kind, looking.person, family)
