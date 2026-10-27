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

import family_view

PAGE_DIR = Path(__file__).parent / "dashboard"

# The page's files and what they are. Anything else under /static is not one.
_ASSETS = {
    "tokens.css": "text/css", "dashboard.css": "text/css",
    "widgets/fs-widget.css": "text/css", "widgets/fs-widget.js": "text/javascript",
    "widgets/fs-activity.css": "text/css", "widgets/fs-activity.js": "text/javascript",
    "widgets/fs-stack.css": "text/css", "widgets/fs-stack.js": "text/javascript",
    "live.js": "text/javascript",
    "family-bar.js": "text/javascript",
    "first-run.js": "text/javascript", "first-run.css": "text/css",
}
# The components the page builds with (`dashboard/components/`), each served
# under its own name; the gallery beside them is not one.
_COMPONENT = re.compile(r"^components/[a-z-]+\.(js|css|json)$")
_KINDS = {"js": "text/javascript", "css": "text/css", "json": "application/json"}
_FONT = re.compile(r"^[\w.-]+\.woff2$")
SIZES = ("small", "medium", "large")

# The card browser's port on this Mac (core's docker-compose.yml publishes
# it on 127.0.0.1 only), for the links a browser on this Mac follows.
CARD_BROWSER_PORT = 42002
_THIS_MAC = {"localhost", "127.0.0.1", "[::1]"}

# The page's own words. The greeting addresses the household the way each
# language does: English by its plural ("Simpsons"), German as "Familie
# Simpson"; `{name}` is that form, set in emphasis.
_PAGE_WORDS = {
    "en": {"title": "Dashboard", "welcome": "Welcome home.", "welcome_named": "Welcome home, {name}",
           "lede": "This Mac is now your family's own server: the chat runs on it, and what you "
                   "add next (photos, letters, memories) stays in this house too.",
           "how_label": "How it works",
           "how": (("chat", "Talk in the chat",
                    "Everyone gets the Element app. Every message goes to this Mac and nowhere else."),
                   ("inbox", "Hand things over",
                    "A letter to the Documents room, a voice note to Memories. It is filed for you."),
                   ("home", "Find it here",
                    "This page shows what came in, what is due and what the family wants to keep."))},
    "de": {"title": "Übersicht", "welcome": "Willkommen zu Hause.",
           "welcome_named": "Willkommen zu Hause, {name}",
           "lede": "Dieser Mac ist jetzt der eigene Server eurer Familie: Der Chat läuft auf ihm, "
                   "und was ihr als Nächstes dazunehmt (Fotos, Briefe, Erinnerungen), bleibt auch "
                   "in diesem Haus.",
           "how_label": "So funktioniert es",
           "how": (("chat", "Im Chat reden",
                    "Alle bekommen die Element-App. Jede Nachricht geht an diesen Mac und sonst nirgendwohin."),
                   ("inbox", "Dinge abgeben",
                    "Ein Brief in den Dokumente-Raum, eine Sprachnachricht in die Erinnerungen. "
                    "Abgelegt wird für euch."),
                   ("home", "Hier finden",
                    "Diese Seite zeigt, was angekommen ist, was ansteht und was die Familie "
                    "behalten will."))},
}

# The three "how it works" icons, drawn like the prototype's (stroke, 24 px grid).
_ICONS = {
    "chat": '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
    "inbox": '<path d="M3 13h5l2 3h4l2-3h5"/><path d="M5.5 5h13L21 13v6H3v-6z"/>',
    "home": '<rect x="3" y="3" width="7.5" height="9" rx="2"/><rect x="13.5" y="3" width="7.5" '
            'height="5" rx="2"/><rect x="13.5" y="11" width="7.5" height="10" rx="2"/>'
            '<rect x="3" y="15" width="7.5" height="6" rx="2"/>',
}


# ── The page ──────────────────────────────────────────────────────────────

def page(widgets: list[str], *, base: str, language: str, family: dict | None = None) -> bytes:
    """The dashboard: the family as pills, its welcome, and `widgets` (each a
    shell's HTML) in its slots. `family` is what `stack family` shows, None
    when the host API did not answer.

    While the family is known to be empty the same page is the first run:
    `first-run.js` sets the family up over it, and the page enters when the
    family is in. Without JavaScript the page says how to do it instead.
    """
    words = _PAGE_WORDS["de" if language == "de" else "en"]
    first_run = family_view.is_empty(family)
    template = Template((PAGE_DIR / "index.html").read_text(encoding="utf-8"))
    return template.substitute(
        lang=language, base=base, title=escape(words["title"]),
        first_run=" data-first-run" if first_run else "",
        first_run_head=_first_run_head(base) if first_run else "",
        pills=family_view.pills(family, language),
        welcome=welcome((family or {}).get("name", ""), language)
                + (_noscript(language) if first_run else ""),
        page_data=family_view.page_data(language, base),
        widgets="\n".join(widgets)).encode()


# What the first run draws with, besides what every page loads.
_FIRST_RUN_STYLES = ("components/brand.css", "components/button.css", "components/field.css",
                     "components/moment.css", "components/segmented.css",
                     "components/family-editor.css", "first-run.css")


def _first_run_head(base: str) -> str:
    links = "".join(f'<link rel="stylesheet" href="{base}/static/{name}">\n  '
                    for name in _FIRST_RUN_STYLES)
    return links + f'<script type="module" src="{base}/static/first-run.js"></script>'



def _noscript(language: str) -> str:
    return f'<noscript><p class="first-run-noscript">{_e(family_view.WORDS[family_view.language(language)]["noscript"])}</p></noscript>'



def welcome(owner: str, language: str) -> str:
    """The greeting, what the Mac now is, and how the stack works."""
    words = _PAGE_WORDS["de" if language == "de" else "en"]
    how = "".join(
        f'<li><span class="how-icon" aria-hidden="true"><svg viewBox="0 0 24 24" width="20" '
        f'height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round">{_ICONS[icon]}</svg></span><div><b class="how-title">{_e(title)}</b>'
        f'<span>{_e(text)}</span></div></li>'
        for icon, title, text in words["how"])
    return (f'<h1 class="greeting">{greeting(owner, language)}</h1>'
            f'<p class="lede">{_e(words["lede"])}</p>'
            f'<ol class="how" aria-label="{_e(words["how_label"])}">{how}</ol>')


def greeting(owner: str, language: str) -> str:
    """"Welcome home, <em>Simpsons.</em>", as HTML; without a name, no hole in it."""
    words = _PAGE_WORDS["de" if language == "de" else "en"]
    name = family_view.called(owner, language)
    if not name:
        return _e(words["welcome"])
    return words["welcome_named"].format(name=f"<em>{_e(name)}.</em>")


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
    if kind is None and _COMPONENT.match(path) and (PAGE_DIR / path).is_file():
        kind = _KINDS[path.rsplit(".", 1)[1]]
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
