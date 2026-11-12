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
from collections.abc import Sequence
from html import escape
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING

import family_view
from stack import links

if TYPE_CHECKING:
    from devices import Looking, Who

PAGE_DIR = Path(__file__).parent / "dashboard"

# The page's files and what they are. Anything else under /static is not one.
_ASSETS = {
    "tokens.css": "text/css", "dashboard.css": "text/css",
    "widgets/fs-widget.css": "text/css", "widgets/fs-widget.js": "text/javascript",
    "widgets/fs-activity.css": "text/css", "widgets/fs-activity.js": "text/javascript",
    "widgets/fs-stack.css": "text/css", "widgets/fs-stack.js": "text/javascript",
    "widgets/fs-status.css": "text/css", "widgets/fs-status.js": "text/javascript",
    "widgets/service-cards.js": "text/javascript",
    "live.js": "text/javascript",
    "scripts-run.js": "text/javascript",
    "family-bar.js": "text/javascript",
    "first-run.js": "text/javascript", "first-run.css": "text/css",
    "setup.css": "text/css", "setup-page.js": "text/javascript",
    "ai-step.js": "text/javascript", "first-words.js": "text/javascript",
    "copy-value.js": "text/javascript", "sign-in-guide.js": "text/javascript", "devices.js": "text/javascript",
}
# The components the page builds with (`dashboard/components/`), each served
# under its own name; the gallery beside them is not one.
_COMPONENT = re.compile(r"^components/[a-z-]+\.(js|css|json)$")
_KINDS = {"js": "text/javascript", "css": "text/css", "json": "application/json"}
_FONT = re.compile(r"^[\w.-]+\.woff2$")
SIZES = ("small", "medium", "large")

# The page's own words. The greeting addresses the household the way each
# language does: English by its plural ("Simpsons"), German as "Familie
# Simpson"; `{name}` is that form, set in emphasis.
_PAGE_WORDS = {
    "en": {"title": "Dashboard", "welcome": "Welcome home.", "welcome_named": "Welcome home, {name}",
           "lede": "This Mac is now your family's own server: the chat runs on it, and what you "
                   "add next (photos, letters, memories) stays in this house too.",
           # On the home address the page is read on a phone or another
           # computer, where "This Mac" would name the wrong device.
           "lede_home": "The Mac at home is your family's own server: the chat runs on it, and what "
                        "you add next (photos, letters, memories) stays in this house too.",
           "lede_before_chat": "This Mac is now your family's own server. Start the chat below; "
                               "it and what you add next (photos, letters, memories) stay in this "
                               "house.",
           "how_label": "How it works",
           # The chat item's text off the Mac's address, by the same rule as `lede_home`.
           "how_home": {"chat": "Everyone gets the Element app. Every message goes to the Mac at home "
                                "and nowhere else."},
           "how": (("chat", "Talk in the chat",
                    "Everyone gets the Element app. Every message goes to this Mac and nowhere else."),
                   ("inbox", "Hand things over",
                    "A letter to the Documents room, a voice note to Memories. It is filed for you."),
                   ("home", "Find it here",
                    "This page shows what came in, what is due and what the family wants to keep."))},
    "de": {"title": "Übersicht", "welcome": "Willkommen zu Hause.",
           "welcome_named": "Willkommen zu Hause, {name}",
           "lede": "Dieser Mac ist jetzt euer Familienserver. Der Chat läuft hier, und alles, was ihr als Nächstes "
                   "hinzufügt (Fotos, Briefe, Erinnerungen), bleibt ebenfalls bei euch zu Hause.",
           "lede_home": "Der Mac zu Hause ist euer Familienserver. Auf ihm läuft der Chat, und alles, was ihr als "
                        "Nächstes hinzufügt (Fotos, Briefe, Erinnerungen), bleibt bei euch zu Hause.",
           "lede_before_chat": "Dieser Mac ist jetzt euer Familienserver. Starte unten den Chat. Der Chat und alles, "
                               "was ihr danach hinzufügt (Fotos, Briefe, Erinnerungen), bleiben bei euch zu Hause.",
           "how_label": "So funktioniert es",
           "how_home": {"chat": "Alle bekommen die Element-App. Jede Nachricht geht nur an den Mac zu Hause."},
           "how": (("chat", "Im Chat schreiben",
                    "Alle bekommen die Element-App. Jede Nachricht geht nur an diesen Mac."),
                   ("inbox", "An famstack schicken",
                    "Ein Brief in den Raum für Dokumente, eine Sprachnachricht in den Raum für Erinnerungen: "
                    "famstack legt alles für euch ab."),
                   ("home", "Hier wiederfinden",
                    "Diese Seite zeigt, was neu ist, was ansteht und was eure Familie behalten will."))},
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

def page(widgets: list[str], *, base: str, language: str, who: Who, looking: Looking,
         family: dict | None = None, chat_runs: bool = True) -> bytes:
    """The dashboard: the family as pills, its welcome, and `widgets` (each a
    shell's HTML) in its slots, for `who` (`devices.Who`). `family` is what
    `stack family` shows, None when the host API did not answer. Only the
    Mac's address (`who.here`) gets the first run and the hint to pick
    one's name (`family_view.device_data`). `looking` is what
    `devices.looking` makes of `who`: the top bar's name on the right (the
    paired device's person, "Homer Simpson", or a shared device's name,
    "Living room"), the links beside it ("Your devices" and "Add a
    device" on a person's paired device), and the name the welcome greets
    ("Welcome home, Homer.").

    While the family is known to be empty the same page on the Mac's
    address is the first run: `first-run.js` sets the family up over it,
    and the page enters when the family is in. Without JavaScript the page
    says how to do it instead. The home address has no first run, because
    it takes no change (`access.mac_only`).
    """
    words = _PAGE_WORDS["de" if language == "de" else "en"]
    first_run = who.here and family_view.is_empty(family)
    device = family_view.device_data(who)
    template = Template((PAGE_DIR / "index.html").read_text(encoding="utf-8"))
    return template.substitute(
        lang=language, base=base, title=escape(words["title"]),
        looking=(f'<span class="who">{escape(looking.name)}</span>' if looking.name else "")
                + _menu(looking.menu),
        first_run=" data-first-run" if first_run else "",
        first_run_head=_first_run_head(base) if first_run else "",
        pills=family_view.pills(family, language, picker=device is None),
        welcome=welcome((family or {}).get("name", ""), language, chat_runs=chat_runs,
                        person=looking.greeting, here=who.here)
                + (_noscript(language) if first_run else ""),
        page_data=family_view.page_data(language, base, device),
        widgets="\n".join(widgets)).encode()


def _menu(links: Sequence[tuple[str, str]]) -> str:
    """The top bar's links beside the name, "" for none."""
    if not links:
        return ""
    return ('<nav class="who-menu">' + "".join(
        f'<a class="fs-link" href="{escape(href)}">{escape(label)}</a>' for label, href in links)
        + "</nav>")


# What the first run draws with, besides what every page loads.
_FIRST_RUN_STYLES = ("components/brand.css", "components/moment.css",
                     "components/family-editor.css", "first-run.css")


def _first_run_head(base: str) -> str:
    links = "".join(f'<link rel="stylesheet" href="{base}/static/{name}">\n  '
                    for name in _FIRST_RUN_STYLES)
    return links + f'<script type="module" src="{base}/static/first-run.js"></script>'



def _noscript(language: str) -> str:
    return f'<noscript><p class="first-run-noscript">{_e(family_view.WORDS[family_view.language(language)]["noscript"])}</p></noscript>'



def welcome(owner: str, language: str, *, chat_runs: bool = True, person: str = "",
            here: bool = True) -> str:
    """The greeting (`greeting`: `person` when the device is theirs, else
    the household `owner`), what the Mac now is, and how the stack works.
    Off the Mac's address (`here` False) the Mac is "the Mac at home", in
    the lede and the chat's how item, because the reader holds another
    device. Before the
    chat runs, the sentence on what the Mac is does not say it does; it
    carries the one for after, which the setup widget puts in when the chat
    comes up, so the page needs no reload."""
    words = _PAGE_WORDS["de" if language == "de" else "en"]
    how = "".join(
        f'<li><span class="how-icon" aria-hidden="true"><svg viewBox="0 0 24 24" width="20" '
        f'height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round">{_ICONS[icon]}</svg></span><div><b class="how-title">{_e(title)}</b>'
        f'<span>{_e(text if here else words["how_home"].get(icon, text))}</span></div></li>'
        for icon, title, text in words["how"])
    lede = words["lede" if here else "lede_home"]
    later = "" if chat_runs else f' data-once-chat-runs="{_e(lede)}"'
    return (f'<h1 class="greeting">{greeting(owner, language, person=person)}</h1>'
            f'<p class="lede"{later}>{_e(lede if chat_runs else words["lede_before_chat"])}</p>'
            f'<ol class="how" aria-label="{_e(words["how_label"])}">{how}</ol>')


def greeting(owner: str, language: str, *, person: str = "") -> str:
    """"Welcome home, <em>Simpsons.</em>", as HTML, or "Welcome home,
    <em>Homer.</em>" for `person`, the first name of the person the device
    is paired as; without a name, no hole in it. The served page cannot
    know the time on the device, so the greeting by time of day is
    `family-bar.js`'s, for the same name."""
    words = _PAGE_WORDS["de" if language == "de" else "en"]
    name = person or family_view.called(owner, language)
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


def card_link(link_prefix: str):
    """The link of a card on the dashboard: its persistent `/go` link, or ""
    for a card with no stable link (a topic, a hand-written page).

    `card` is a card as the memory's history names it, with the logical
    `link` the memory made for it (`stack.links.go_page`). The link lands
    on the card's page, which reads the memory as it is this second, so
    the same link serves this Mac and every other device in the house.
    Every card link on the dashboard is made here.

        >>> card_link("go")({"id": "note-7f3a9c21e4", "link": "/note-7f3a9c21e4"})
        '/go/card/note-7f3a9c21e4'
        >>> card_link("go")({"id": "family/camping.md", "link": ""})
        ''
    """
    return lambda card: links.public(card.get("link") or "", f"/{link_prefix}")



def font_url(base: str, name: str) -> str | None:
    """The cards API's URL of one of the wiki's fonts, or None for anything else."""
    return f"{base}/fonts/{name}" if _FONT.match(name) else None


def _e(text: str) -> str:
    return escape(str(text), quote=True)
