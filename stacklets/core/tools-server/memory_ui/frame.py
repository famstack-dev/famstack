"""The frame every card page sits in, and the line icons it draws.

On a computer the frame is the wiki's: a column on the left with the
famstack mark and "‹ Dashboard", and the page as a paper sheet beside
it. On a phone the column becomes a bar on top with the same two (the
stylesheet decides, by width).

    html = document(sheet_html, title="Duff Insurance policy", language="en")

The frame has no search and no link to an overview of the memory: a card
page is opened from a link to that one card, and the memory's overview
is its own page, apart from the card pages. The script
(`static/memory.js`) only loads a card's history when it is opened.

The look is the family wiki's (the memory stacklet's Quartz theme), copied
into `static/memory.css`: a card page and a wiki page read as one memory.

Pure: no I/O.
"""

from __future__ import annotations

from html import escape

from access import DASHBOARD_PREFIX
from memory_ui.addresses import asset_address
from memory_ui.words import words

__all__ = ["document", "icon"]


def document(sheet: str, *, title: str, language: str, description: str = "") -> str:
    """A whole page: the frame around `sheet` (HTML). `title` names the
    browser tab; `description` is the preview a chat app shows for a link.

        >>> "<title>Grill · famstack</title>" in document("<p>x</p>", title="Grill", language="en")
        True
    """
    w = words(language)
    meta = (f'<meta name="description" content="{escape(description)}">'
            f'<meta property="og:title" content="{escape(title)}">'
            f'<meta property="og:description" content="{escape(description)}">' if description else "")
    return (
        f'<!doctype html><html lang="{"de" if language == "de" else "en"}"><head>'
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{escape(title)} · famstack</title>{meta}<link rel="icon" href="data:,">'
        f'<link rel="stylesheet" href="{asset_address("memory.css")}"></head><body>'
        '<div class="shell"><aside class="side">'
        f'<div class="ftitle"><p class="byline">{_wordmark()}</p></div>'
        f'<a class="fn-link" href="{DASHBOARD_PREFIX}">{escape(w["dashboard"])}</a></aside>'
        f'<main class="center" id="main">{sheet}</main></div>'
        f'<script src="{asset_address("memory.js")}"></script></body></html>')


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


def _wordmark() -> str:
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
}
