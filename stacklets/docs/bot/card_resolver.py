"""CardResolver: ask core's resolver where a card lives now.

A card's path changes (a refile, a move to another folder, a photo moved
from bookmarks to notes); its card id does not. Core's tools server is
the one place that finds a card by its id: it serves the `/go/` links,
and answers the same lookup as data on its internal listener
(`/cards/<kind>/<id>`). The archivist asks it instead of scanning the
vault itself, so the stack has one scanner and one answer.

The resolver reads the wiki's copy of the family memory, which follows a
write by a few minutes. A lookup that cannot be answered (core down, the
memory not projected yet, a card filed a moment ago) is None, and the
caller falls back to whatever path it already had.
"""

from __future__ import annotations

from urllib.parse import quote

import aiohttp
from loguru import logger

# A correction waits on this answer; the scan behind it is local and
# fast, so anything slower means the resolver is not answering.
_TIMEOUT = aiohttp.ClientTimeout(total=10)


class CardResolver:
    """Where a card is now, by its kind and card id.

        cards = CardResolver(http, os.environ["CARD_RESOLVER_URL"])
        path = await cards.path_of("capture", capture_id)
        # 'family/painting/notes/2026/10/raku-454811.md', or None
    """

    def __init__(self, http: aiohttp.ClientSession, url: str):
        self._http = http
        self._url = url.rstrip("/")

    async def path_of(self, kind: str, card_id: str) -> str | None:
        """The card's vault path, or None when the resolver cannot say.

        None covers an id no card carries (404) and a resolver that does
        not answer at all; the log line tells the two apart.
        """
        if not (self._url and card_id):
            return None
        url = f"{self._url}/cards/{quote(kind, safe='')}/{quote(card_id, safe='')}"
        try:
            async with self._http.get(url, timeout=_TIMEOUT) as resp:
                if resp.status == 404:
                    logger.info("[cards] no {} carries the id {}", kind, card_id)
                    return None
                if resp.status != 200:
                    logger.warning("[cards] resolver answered {} for {} {}",
                                   resp.status, kind, card_id)
                    return None
                body = await resp.json()
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError) as e:
            logger.warning("[cards] resolver unreachable for {} {}: {}", kind, card_id, e)
            return None
        path = body.get("path") if isinstance(body, dict) else None
        return path if isinstance(path, str) and path else None
