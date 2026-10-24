"""The card browser's page, and where its reads and writes go.

The page is three files (`cards/`), served as they are: no build step, no
request leaves the house. Its reads go to the memory stacklet's cards API
(`stacklets/memory/cards_api.py`) on the stack network; this server passes
them through, so a browser never reaches the memory stacklet.

A change to a card (`PATCH /cards/api/cards/<card id>`) is passed
on the same way, with the headers the memory stacklet decides it by: the
write token, the version it was read at, who it is by. This server adds
no token and checks none, although core's `.env` carries one for the bot
runner: a write from this Mac is the client's, with the client's token.
The memory stacklet refuses a write without it (`stack memory card edit`,
docs/stack-reference.md).

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
_ROUTES = re.compile(r"^(find|people|history|report|card/.+|cards/[^/]+|cards/[^/]+/versions"
                     r"|links/.+|pending|files/.+|tree/.+|paths(/.+)?|fonts/[\w.-]+\.woff2|media/[\w./-]+)$")
# The routes a client may change, by method: one card by its id, and the
# card store's writes.
_WRITE_ROUTES = {"PATCH": re.compile(r"^cards/[^/]+$"), "POST": re.compile(r"^(commits|sync)$")}
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


def write_upstream(method: str, path: str, base: str = MEMORY_CARDS_URL) -> str | None:
    """The memory API's URL for a change by `method` to `path`, or None if it is not one."""
    path = path.strip("/")
    route = _WRITE_ROUTES.get(method)
    if ".." in path.split("/") or route is None or not route.match(path):
        return None
    return f"{base}/{path}"


def forwarded(headers: Mapping[str, str]) -> dict[str, str]:
    """The headers of a client's request that go on to the memory API (`FORWARDED`)."""
    return {name: headers[name] for name in FORWARDED if name in headers}
