"""What the memory's pages read from the memory: its cards API over HTTP,
and its repository's history for a removed card.

The memory stacklet owns the cards and serves them (its cards API,
`stacklets/memory/cards_api.py`); core's pages only read. Every read here
goes through the route check of `cards_page.upstream`, with the pages' own
list (`PAGE_ROUTES`): the routes these reads use, on the stack network.
A browser's list (`cards_page.BROWSER_ROUTES`) is narrower, because the
pages render here and a browser reads only their media and fonts.

    memory = Reads("http://stack-memory-curator:8090")
    status, card = await memory.card("note-0b6e2d91aa")
    names = await memory.names()            # {"homer": "Homer", ...}
    kept = await memory.kept("2026/11/Q7uno.pdf")   # {"filename": ..., "size": ...}
    changes = await memory.history(limit=5)          # the dashboard's widget, the home

    memory = Reads("http://stack-memory-curator:8090", vault=Path("/vault"))
    gone = await memory.removal("note-6e5d4c3b2a")   # Removal(who="marge", day="2026-11-03", ...)

A memory that does not answer is not an error here: each read says so
with None, and the page says the memory does not answer.

A removed card is the one read that does not go through the cards API:
the API knows the cards there are, not the ones there were. Who removed
a card is in the memory's git history, which core reads from its
read-only mount of the memory's repository (`stack.cards.lookup.removal_of`).

The I/O edge of the pages and of the dashboard's activity widget; the
pages themselves are pure (`card_page.py`, `home.py`, `history.py`), and
`pages.py` joins the reads to them.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import quote

import httpx

import cards_page
from stack.cards.lookup import Removal, removal_of

__all__ = ["Reads"]

# The cards API's routes the pages read, one per method of `Reads` below.
# A route no method uses stays off it, so a page never asks for more than
# it shows.
PAGE_ROUTES = re.compile(r"^(card/.+|people|history|find|cards/[^/]+/versions"
                         r"|media/[\w./-]+\.json)$")
# A read of one card or a list waits this long; the history, git, longer.
TIMEOUT_SECS = 10
HISTORY_TIMEOUT_SECS = 30
# How many cards a search or a filter lists, and how many changes the home shows.
FOUND_LIMIT = 50
CHANGES_LIMIT = 20


class Reads:
    """The memory's cards API at `base`, read for the pages, and the
    memory's repository at `vault` for who removed a card.

        status, card = await Reads(base).card("person-homer")   # (200, {...})
    """

    def __init__(self, base: str, *, vault: Path | None = None):
        self.base = base
        self.vault = vault

    async def card(self, ref: str) -> tuple[int | None, dict | None]:
        """The status the memory answers `card/<ref>` with, and the card;
        (None, None) when it does not answer."""
        status, body = await self._get(f"card/{quote(ref)}")
        return status, (body if status == 200 else None)

    async def removal(self, card_id: str) -> Removal | None:
        """Who removed the card with `card_id` and when, from the memory's
        history; None for an id no card ever had, a text that is no card
        id, or no repository to read. Ask it only after the cards API
        found no card: a card written again after its removal is there.
        git runs in a worker thread, not on the loop."""
        if self.vault is None:
            return None
        return await asyncio.to_thread(removal_of, card_id, repo=self.vault)

    async def names(self) -> dict[str, str]:
        """Each person's id and the name a reader knows them by, from their
        card: `{"homer": "Homer"}` for `family/people/homer.md`, "Homer Simpson"."""
        _, body = await self._get("people")
        names = {}
        for person in (body or {}).get("people", []):
            slug = str(person.get("path") or "").rsplit("/", 1)[-1].removesuffix(".md")
            title = str(person.get("title") or "").strip()
            if slug and title:
                names[slug.lower()] = title.split()[0]
        return names

    async def history(self, limit: int = CHANGES_LIMIT, audience: str = "") -> dict | None:
        """What changed lately (`/history`), None when the memory does not answer.

        It asks for one change more than `limit`, so a reader knows whether
        there is more. `audience` ("family", "homer") narrows it to what one
        audience may see; the home shows all, the dashboard's widget may not.

            await memory.history(limit=5, audience="family")   # {"changes": [...]}
        """
        params = {"limit": str(limit + 1), **({"audience": audience} if audience else {})}
        status, body = await self._get("history", params, timeout=HISTORY_TIMEOUT_SECS)
        return body if status == 200 else None

    async def find(self, query: str, kind: str) -> list[dict] | None:
        """The cards a search and a type filter find, newest first when only
        a type is asked; None when the memory does not answer."""
        params = {k: v for k, v in {"q": query, "type": kind, "limit": str(FOUND_LIMIT),
                                    "sort": "" if query else "newest"}.items() if v}
        status, body = await self._get("find", params)
        return (body or {}).get("cards", []) if status == 200 else None

    async def lists(self) -> list[dict]:
        """The family's lists, each read whole for its items."""
        found = await self.find("", "list") or []
        cards = [await self.card(c["id"]) for c in found]
        return [card for _, card in cards if card]

    async def versions(self, card_id: str) -> list[dict] | None:
        """The card's versions, oldest first; None when the memory does not
        answer or has no such card."""
        status, body = await self._get(f"cards/{quote(card_id, safe='')}/versions",
                                       timeout=HISTORY_TIMEOUT_SECS)
        return (body or {}).get("versions", []) if status == 200 else None

    async def kept(self, path: str) -> dict | None:
        """What the media store knows of its file `path` (`2026/11/x.pdf`):
        the `.json` beside it, with the file's `filename`, `mime` and `size`
        (docs/memory-format.md, "Media"). None when the store has no such
        file, or no record of it, or the memory does not answer: a copy of
        the memory without its files shows no panel for it."""
        stem, dot, _ = path.rpartition(".")
        if not dot:
            return None
        status, body = await self._get(f"media/{stem}.json")
        return body if status == 200 and isinstance(body, dict) else None

    async def _get(self, path: str, params: dict | None = None, *,
                   timeout: float = TIMEOUT_SECS) -> tuple[int | None, dict | None]:
        url = cards_page.upstream(path, params or {}, self.base, routes=PAGE_ROUTES)
        if url is None:
            return 404, None
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(url)
        except httpx.HTTPError:
            return None, None
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, None
