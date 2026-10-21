"""The card browser's page, and where its reads and writes go.

The page is three files (`cards/`), served as they are: no build step, no
request leaves the house. Its reads go to the memory stacklet's cards API
(`stacklets/memory/cards_api.py`) on the stack network; this server passes
them through, so a browser never reaches the memory stacklet.

A change to a card (`PATCH` or `PUT /cards/api/cards/<card id>`) is passed
on the same way, with the headers the memory stacklet decides it by: the
write token, the version it was read at, who it is by. Core holds no token
of its own and checks none: the bot runner reads core's `.env` too, so a
secret there would be the bots' as well. The memory stacklet refuses a
write without the token (`stack memory card edit`, docs/stack-reference.md).

Pure: no web framework, so it is tested on its own.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlencode

PAGE_DIR = Path(__file__).parent / "cards"
MEMORY_CARDS_URL = "http://stack-memory-curator:8090"

# The page's files and what they are. Anything else under /cards/static is not one.
_ASSETS = {"cards.css": "text/css", "cards.js": "text/javascript"}
# The memory API's routes a page may read.
_ROUTES = re.compile(r"^(find|people|history|report|card/.+|cards/[^/]+|links/.+"
                     r"|fonts/[\w.-]+\.woff2|media/[\w./-]+)$")
# The routes a client may change: one card, by its id.
_WRITE_ROUTES = re.compile(r"^cards/[^/]+$")
# The request headers a write is decided by, passed on as they came; and the
# one a read of a card asks for markdown with.
FORWARDED = ("authorization", "content-type", "if-match", "famstack-actor", "accept")


def page() -> bytes:
    return (PAGE_DIR / "index.html").read_bytes()


def asset(name: str) -> tuple[str, bytes] | None:
    """(content type, bytes) of one of the page's files, or None."""
    kind = _ASSETS.get(name)
    return (kind, (PAGE_DIR / name).read_bytes()) if kind else None


def upstream(path: str, query: dict[str, str], base: str = MEMORY_CARDS_URL) -> str | None:
    """The memory API's URL for a page's read of `path`, or None if it is not one."""
    path = path.strip("/")
    if ".." in path.split("/") or not _ROUTES.match(path):
        return None
    return f"{base}/{path}" + (f"?{urlencode(query)}" if query else "")


def write_upstream(path: str, base: str = MEMORY_CARDS_URL) -> str | None:
    """The memory API's URL for a change to the card at `path`, or None if it is not one."""
    path = path.strip("/")
    if ".." in path.split("/") or not _WRITE_ROUTES.match(path):
        return None
    return f"{base}/{path}"


def forwarded(headers: Mapping[str, str]) -> dict[str, str]:
    """The headers of a client's request that go on to the memory API (`FORWARDED`)."""
    return {name: headers[name] for name in FORWARDED if name in headers}
