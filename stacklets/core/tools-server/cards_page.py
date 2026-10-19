"""The card browser's page, and where its reads go.

The page is three files (`cards/`), served as they are: no build step, no
request leaves the house. Its reads go to the memory stacklet's cards API
(`stacklets/memory/cards_api.py`) on the stack network; this server passes
them through, GET only, so a browser never reaches the memory stacklet.

Pure: no web framework, so it is tested on its own.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlencode

PAGE_DIR = Path(__file__).parent / "cards"
MEMORY_CARDS_URL = "http://stack-memory-curator:8090"

# The page's files and what they are. Anything else under /cards/static is not one.
_ASSETS = {"cards.css": "text/css", "cards.js": "text/javascript"}
# The memory API's routes a page may read.
_ROUTES = re.compile(r"^(find|people|history|report|card/.+|links/.+|fonts/[\w.-]+\.woff2|media/[\w./-]+)$")


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
