"""The dashboard's page: its widgets in their shells, and what it loads.

A first version of the dashboard RFC-001 describes, smaller on purpose:
one page in core's tools server with one widget (`activity_widget.py`),
rather than a stacklet of its own. When the dashboard grows its own
stacklet (`stacklets/dashboard/`, RFC-001), this module, the widget
modules and `dashboard/` move there; the access rule's prefix goes with
them.

The page is served with every widget already rendered, so it reads
complete without JavaScript; each widget's script then enhances it
(`dashboard/widgets/README.md` has the contract). Its files are served as
they are, no build step, and nothing is loaded from outside the house:
the fonts are the wiki's, read through the memory's cards API.

Pure: no web framework, so it is tested through the server's routes and
nothing here needs one.
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path
from string import Template
from urllib.parse import quote, urlencode, urlsplit

PAGE_DIR = Path(__file__).parent / "dashboard"

# The page's files and what they are. Anything else under /static is not one.
_ASSETS = {
    "tokens.css": "text/css", "dashboard.css": "text/css",
    "widgets/fs-widget.css": "text/css", "widgets/fs-widget.js": "text/javascript",
    "widgets/fs-activity.css": "text/css", "widgets/fs-activity.js": "text/javascript",
}
_FONT = re.compile(r"^[\w.-]+\.woff2$")
SIZES = ("small", "medium", "large")

# The card browser's port on this Mac (core's docker-compose.yml publishes
# it on 127.0.0.1 only), for the links a browser on this Mac follows.
CARD_BROWSER_PORT = 42002
_THIS_MAC = {"localhost", "127.0.0.1", "[::1]"}

_PAGE_WORDS = {"en": {"title": "Dashboard", "heading": "What's new"},
               "de": {"title": "Übersicht", "heading": "Was es Neues gibt"}}


# ── The page ──────────────────────────────────────────────────────────────

def page(widgets: list[str], *, base: str, language: str) -> bytes:
    """The dashboard with `widgets` (each a shell's HTML) in its slots."""
    words = _PAGE_WORDS["de" if language == "de" else "en"]
    template = Template((PAGE_DIR / "index.html").read_text(encoding="utf-8"))
    return template.substitute(
        lang=language, base=base, title=escape(words["title"]), heading=escape(words["heading"]),
        widgets="\n".join(widgets)).encode()


def shell(view: dict, content: str, *, name: str, size: str = "", refresh: int = 60) -> str:
    """A widget in its shell, the frame every widget shares: its title,
    its link, and the parts for its states (content, empty, error,
    loading). `view` is the widget's view (its `state`, `src` and words),
    `content` its rendered content, `name` its element (`activity` is
    `<fs-activity>`). `size` fixes the widget's size instead of measuring
    its slot."""
    see_all = view.get("see_all")
    link = (f'<a class="fs-link" data-show="{see_all["show"]}" href="{_e(see_all["href"])}">'
            f'{_e(see_all["label"])}</a>') if see_all else ""
    sized = f' size="{size}"' if size in SIZES else ""
    return (
        f'<fs-widget class="fs-widget" data-widget="{name}" state="{view["state"]}" '
        f'src="{_e(view["src"])}" refresh="{refresh}"{sized}>'
        f'<header class="fs-head"><h2 class="fs-title">{_e(view["title"])}</h2>{link}</header>'
        f'<div class="fs-content" data-state="content">{content}</div>'
        f'<p class="fs-empty" data-state="empty">{_e(view["empty"])}</p>'
        f'<div class="fs-error" data-state="error" role="alert"><p>{_e(view["error"])}</p>'
        f'<a class="fs-retry" href="{_e(view["retry_href"])}">{_e(view["retry"])}</a></div>'
        '<div class="fs-skeleton" data-state="loading" aria-hidden="true">'
        '<span></span><span></span><span></span></div>'
        '</fs-widget>')


def asset(path: str) -> tuple[str, bytes] | None:
    """(content type, bytes) of one of the page's files, or None."""
    kind = _ASSETS.get(path)
    return (kind, (PAGE_DIR / path).read_bytes()) if kind else None


# ── What a request asks for ──────────────────────────────────────────────

def language_of(accept_language: str) -> str:
    """German for a browser that asks for it first, else English, as the
    card browser decides."""
    first = accept_language.split(",", 1)[0].strip().lower()
    return "de" if first.startswith("de") else "en"


def limit_of(value: str | None, default: int, most: int) -> int:
    try:
        return max(1, min(int(value or default), most))
    except ValueError:
        return default


def card_link(host: str, link_prefix: str):
    """The link of a card for a browser that addressed the server as `host`.

    On this Mac a card opens in the card browser: it reads the memory as
    it is this second. The wiki behind `/<prefix>/<card_id>` is compiled a
    few minutes after a change, so the newest card on the dashboard would
    not open there yet. Any other device cannot reach the card browser
    (published on 127.0.0.1), so it gets the persistent link, which opens
    the card in the wiki from anywhere in the house.
    """
    hostname = urlsplit(f"//{host}").hostname or ""
    shown = f"[{hostname}]" if ":" in hostname else hostname
    if shown in _THIS_MAC:
        return lambda card: (f"http://{shown}:{CARD_BROWSER_PORT}/cards/"
                             f"#card={quote(card['id'], safe='/')}")
    return lambda card: f"/{link_prefix}{card['link']}" if card.get("link") else ""


def history_url(base: str, *, limit: int, audience: str) -> str:
    """The cards API's history for one more change than the widget shows,
    so it knows whether there is more."""
    query = {"limit": limit + 1, **({"audience": audience} if audience else {})}
    return f"{base}/history?{urlencode(query)}"


def font_url(base: str, name: str) -> str | None:
    """The cards API's URL of one of the wiki's fonts, or None for anything else."""
    return f"{base}/fonts/{name}" if _FONT.match(name) else None


def _e(text: str) -> str:
    return escape(str(text), quote=True)
