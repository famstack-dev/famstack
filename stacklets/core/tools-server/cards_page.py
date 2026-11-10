"""The memory pages' files, and where a browser's reads and writes go.

The card pages (`/card/<card id>`) are rendered on this
server (`memory_ui`); their stylesheet and script (`memory_ui/static`) are
served as they are: no build step, no request leaves the house. A browser's
own reads of the memory (`/card/api/<route>`) go to the memory stacklet's
cards API (`stacklets/memory/cards_api.py`) on the stack network; this
server passes them through, so a browser never reaches the memory stacklet.

Each caller has its own list of the read routes it may pass, because they
are reached from different places:

    upstream("media/2026/11/x.pdf", {}, routes=BROWSER_ROUTES)  # a phone: passed
    upstream("tree/family", {}, routes=BROWSER_ROUTES)          # a phone: None
    upstream("tree/family", {}, routes=MAC_ROUTES)              # this Mac: passed

`/card/api` is open to every device on the home network, and the pages
render on the server, so a browser there reads only the pages' media and
fonts (`BROWSER_ROUTES`). `/cards/api` is published on this Mac only, for
the host's commands, and reads the whole store (`MAC_ROUTES`). The pages'
own reads on the stack network keep their list in `memory_ui/reads.py`.

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

from stack.memory import CURATOR_URL as MEMORY_CARDS_URL

# The page the old address `/cards` answers (`forwarding_page`).
PAGE_DIR = Path(__file__).parent / "cards"
# The memory pages' files (`memory_ui/static`) and what they are. Anything
# else under /card/static is not one.
STATIC_DIR = Path(__file__).parent / "memory_ui" / "static"
_ASSETS = {"memory.css": "text/css", "memory.js": "text/javascript"}
# The memory API's read routes this Mac's commands may ask for, through
# `/cards/api` on the listener published on this Mac only: all of them.
MAC_ROUTES = re.compile(r"^(find|people|history|report|card/.+|cards/[^/]+|cards/[^/]+/versions"
                        r"|links/.+|pending|files/.+|tree/.+|paths(/.+)?|fonts/[\w.-]+\.woff2"
                        r"|media/[\w./-]+)$")
# The read routes a browser on the home network may ask for, through
# `/card/api`: a page's media (`memory_ui/addresses.media_address`) and
# fonts (`memory_ui/static/memory.css`), nothing else. Keep it that narrow:
# every device in the house reaches it, and nothing on that listener knows
# who is looking, so a wider list hands the store's files, paths and sync
# state to any of them.
BROWSER_ROUTES = re.compile(r"^(fonts/[\w.-]+\.woff2|media/[\w./-]+)$")
# The routes a client may change, by method: one card by its id, and the
# card store's writes.
_WRITE_ROUTES = {"PATCH": re.compile(r"^cards/[^/]+$"), "POST": re.compile(r"^(commits|sync)$")}
# The request headers a write is decided by, passed on as they came; and the
# one a read of a card asks for markdown with.
FORWARDED = ("authorization", "content-type", "if-match", "famstack-actor", "accept")


def asset(name: str) -> tuple[str, bytes] | None:
    """(content type, bytes) of one of the page's files, or None."""
    kind = _ASSETS.get(name)
    return (kind, (STATIC_DIR / name).read_bytes()) if kind else None


def upstream(path: str, query: dict[str, str], base: str = MEMORY_CARDS_URL, *,
             routes: re.Pattern[str]) -> str | None:
    """The memory API's URL for a read of `path`, or None when `routes`, the
    caller's list (`MAC_ROUTES`, `BROWSER_ROUTES`), does not hold it."""
    path = path.strip("/")
    if ".." in path.split("/") or not routes.match(path):
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


def forwarding_page() -> bytes:
    """The page the old address `/cards` answers: its script goes on to the
    card's address, `/cards#card=note-0b6e2d91aa&q=grill` to
    `/card/note-0b6e2d91aa#q=grill`; `/cards` alone stays and says to
    open a card from its link in the chat, as there is no page for all
    cards. A card named by its file's path (`#card=homer/notes/grill.md`)
    goes to `/card/homer/notes/grill.md`, which `memory_ui.pages.landing` sends
    on to its id.

    Why in the browser: the card browser named the open card after the
    `#`, and a browser never sends that part to a server, so only a
    script on the page can read it.
    """
    return (PAGE_DIR / "forward.html").read_bytes()
