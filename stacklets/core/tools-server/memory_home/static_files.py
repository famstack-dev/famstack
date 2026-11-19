"""The memory pages' own files (`static/`): the stylesheet, the small script, the card links' preview, the search's layer and its phone toggle.

    kind, body = asset("home.css")      # ("text/css", b"...")
    asset("../server.py")               # None: not one of the pages' files

Only the names listed here are served, so `/memory/static/<name>` can never
reach another file of the server.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["asset", "STATIC_DIR"]

STATIC_DIR = Path(__file__).parent / "static"
# The pages' own files and what they are. Anything else under
# `/memory/static` is not one.
_ASSETS = {"home.css": "text/css", "home.js": "text/javascript", "preview.js": "text/javascript",
           "search_layer.js": "text/javascript", "search_toggle.js": "text/javascript"}


def asset(name: str) -> tuple[str, bytes] | None:
    """(content type, bytes) of one of the pages' files, or None."""
    kind = _ASSETS.get(name)
    return (kind, (STATIC_DIR / name).read_bytes()) if kind else None
