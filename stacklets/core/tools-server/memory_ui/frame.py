"""The frame every card page sits in, and the line icons it draws.

On a computer the frame is the wiki's: a column on the left, and the page
as a paper sheet beside it. On a phone the column folds away (the
stylesheets decide, by width). What the column and the header above it
hold is the memory's pages' (`memory_home.frame`): the memory's header
with its one search, and the menu of the device's `/memory`.

    html = document(sheet_html, title="My memory", language="en", header=head_html,
                    side=menu_html, stylesheets=("/memory/static/home.css",))

**A card page is drawn in a frame it is given** (`Frame`), which it asks
for (`Framing`) only when it draws a page. A card is one
step in the memory's graph, so its page keeps the memory's header and
menu, which only `memory_home` knows how to read for a device. The card
pages take that frame as a function and never import `memory_home`, so
the dependency runs one way: the memory's pages use the card pages, not
the reverse. The script (`static/memory.js`) only loads a card's history
when it is opened.

The look is the family wiki's (the memory stacklet's Quartz theme), copied
into `static/memory.css`: a card page and a wiki page read as one memory.

Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from html import escape
from typing import Protocol

from memory_ui.addresses import asset_address

__all__ = ["Frame", "Framing", "document", "icon", "wordmark"]


class Frame(Protocol):
    """What draws a whole page around a card page's sheet (HTML): the
    memory's header and menu for the device that asks, in its language.
    `title` names the browser tab, `description` is the preview a chat
    app shows for a link to the page.

        frame('<article class="sheet card">...</article>', title="Grill", description="Buy charcoal.")
        # '<!doctype html>...'
    """

    def __call__(self, sheet: str, *, title: str, description: str = "") -> str: ...


# Reads the frame for the device, and is asked only when a page is drawn:
# a redirect draws none, so it reads nothing for a frame.
Framing = Callable[[], Awaitable[Frame]]


def document(sheet: str, *, title: str, language: str, description: str = "",
             header: str = "", side: str = "", stylesheets: tuple[str, ...] = (),
             scripts: tuple[str, ...] = ()) -> str:
    """A whole page: the frame around `sheet` (HTML). `title` names the
    browser tab; `description` is the preview a chat app shows for a link.
    `header` (HTML) goes above the frame, `side` (HTML) fills the column,
    and `stylesheets` and `scripts` are loaded after the card pages' own.

        >>> "<title>Grill · famstack</title>" in document("<p>x</p>", title="Grill", language="en")
        True
    """
    styles = "".join(f'<link rel="stylesheet" href="{escape(s)}">' for s in stylesheets)
    more = "".join(f'<script src="{escape(s)}"></script>' for s in scripts)
    meta = (f'<meta name="description" content="{escape(description)}">'
            f'<meta property="og:title" content="{escape(title)}">'
            f'<meta property="og:description" content="{escape(description)}">' if description else "")
    return (
        f'<!doctype html><html lang="{"de" if language == "de" else "en"}"><head>'
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{escape(title)} · famstack</title>{meta}<link rel="icon" href="data:,">'
        f'<link rel="stylesheet" href="{asset_address("memory.css")}">{styles}</head><body>'
        f'{header}<div class="shell"><aside class="side">{side}</aside>'
        f'<main class="center" id="main">{sheet}</main></div>'
        f'<script src="{asset_address("memory.js")}"></script>{more}</body></html>')


def icon(name: str, size: int = 14) -> str:
    """A thin line icon, drawn inline in the label's colour; "" for a name
    it does not have.

        >>> icon("bookmark").startswith('<svg class="icon"')
        True
    """
    paths = ICONS.get(name)
    if not paths:
        return ""
    return (f'<svg class="icon" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true" data-icon="{name}">{paths}</svg>')


def wordmark() -> str:
    """The famstack mark, as the wiki writes it: "fam" and "stack" with the
    dotted "a"."""
    return ('<span class="fs-brand" role="img" aria-label="famstack">fam'
            '<span>st<span class="fs-brand-a">a</span>ck</span></span>')


# The mock's icons, one per card type (and a diary entry's medium).
ICONS = {
    "document": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 15h6M9 18h4"/>',
    "note": '<path d="M4 20l4-1L19 8l-3-3L5 16z"/><path d="M14 7l3 3"/>',
    "bookmark": '<path d="M6 3h12v18l-6-4-6 4z"/>',
    "email": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    "diary-voice": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    "diary-photo": '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="2"/>'
                   '<path d="m21 16-5-5-9 8"/>',
    "diary-video": '<rect x="3" y="5" width="18" height="14" rx="2"/>'
                   '<path d="M7 5v14M17 5v14M3 9h4M3 15h4M17 9h4M17 15h4"/>',
    "diary-text": '<path d="M4 7h16M4 12h16M4 17h10"/>',
    "list": '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="m8 12 3 3 5-6"/>',
    "person": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "correspondent": '<path d="M4 21V5l8-2v18M12 9l8 2v10M2 21h20"/>'
                     '<path d="M7 8h2M7 12h2M7 16h2M15 14h2M15 17h2"/>',
    "topic": '<path d="M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>',
    # A diary entry's sections (`entry.py`): photos, a reply, back to all photos.
    "photos": '<rect x="7" y="7" width="14" height="12" rx="2"/><path d="M3 15V5a2 2 0 0 1 2-2h10"/>'
              '<path d="m21 16-4-4-6 6"/>',
    "reply": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 6 6v4"/>',
    "grid": '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/>'
            '<rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    "clip": '<path d="m20 11-8.5 8.5a5 5 0 0 1-7-7L13 4a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L14 7"/>',
    # Kinds of document (a document card's `document_type`, lowercase), in
    # the documents' one colour on the memory's pages; a kind without an
    # icon of its own is drawn as "document".
    "receipt": '<path d="M6 3h12v18l-2-1.5-2 1.5-2-1.5-2 1.5-2-1.5-2 1.5z"/><path d="M9 8h6M9 12h6M9 16h3"/>',
    "invoice": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path d="M12 9.5v9M14 11.5c0-.8-.9-1.5-2-1.5'
               's-2 .7-2 1.5.9 1.5 2 1.5 2 .7 2 1.5-.9 1.5-2 1.5-2-.7-2-1.5"/>',
    "letter": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    "policy": '<path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/><path d="m9 12 2 2 4-4"/>',
    "statement": '<path d="M3 10 12 4l9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18"/>',
    "payslip": '<rect x="3" y="6" width="18" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/>'
               '<path d="M6.5 9.5h.01M17.5 14.5h.01"/>',
    "membership": '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="11" r="2"/>'
                  '<path d="M5.5 16c.6-1.4 1.9-2 3.5-2s2.9.6 3.5 2M15 10h3M15 13h3"/>',
    "registration": '<path d="M9 3h6v3H9z"/><path d="M8 4.5H5V21h14V4.5h-3"/><path d="m9 14 2 2 4-4"/>',
    # The memory's pages (`memory_home`): the family, the search, the phone's menu.
    "family": '<path d="M3 11 12 4l9 7M5 10v10h14V10"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
}
