"""The senders' page: every sender of a document the device may read.

The menu's "Senders" opens `/memory/senders`, drawn from the memory's
overview of the senders (`/overview/senders`):

    title, sheet = senders_page.sheet(answer, context)

The page says how many senders there are, the count the menu shows, then
lists them, most documents first: each row says how many documents came
from the sender, links to the sender's page (`/memory/sender/<card id>`)
and names the kinds of their documents. A sender that sent only another
person's own papers is not in the memory's answer, so it is not here.

Pure: no I/O.
"""

from __future__ import annotations

from html import escape

from memory_home import addresses, sections
from memory_home.frame import HEADING_ID, page_head
from memory_home.items import Context, type_icon
from memory_home.words import counted, words

__all__ = ["sheet"]


def sheet(answer: dict, context: Context) -> tuple[str, str]:
    """(the browser tab's title, the page's sheet) for the overview
    `answer` of the senders.

        sheet({"senders": [{"id": "correspondent-kwik-e-mart", "title": "Kwik-E-Mart",
                            "count": 1, "kinds": ["Receipt"]}]}, context)
        # ("Senders", '<article class="sheet home">... 1 sender ... Kwik-E-Mart ...')
    """
    w = words(context.language)
    found = answer.get("senders") or []
    head = page_head("correspondent", w["everything"], w["senders"],
                     f'<span class="count-line">{escape(counted(len(found), w["sender_count"]))}</span>')
    rows = (sections.rows(_row(s, w) for s in found) if found
            else f'<p class="empty-text">{escape(w["empty"]["senders"])}</p>')
    # The rows are the whole page, so they sit under its heading, not one of their own.
    body = f'<section class="section fl-section" data-section="senders" aria-labelledby="{HEADING_ID}">{rows}</section>'
    return w["senders"], f'<article class="sheet home">{head}<div class="sections">{body}</div></article>'


def _row(sender: dict, w: dict) -> str:
    """One sender as a row: how many documents, the sender's name linking
    its page, and the kinds of its documents under it."""
    kinds = ", ".join(sender.get("kinds", []))
    return (f'<li class="row"><span class="fl-date">{escape(counted(sender.get("count", 0), w["document_count"]))}'
            f'</span>{type_icon("correspondent")}<span class="fl-main"><span class="change-line">'
            f'<a class="internal" href="{escape(addresses.sender(sender["id"]))}">'
            f'{escape(sender.get("title") or sender["id"])}</a></span>'
            + (f'<span class="fl-meta">{escape(kinds)}</span>' if kinds else "") + "</span></li>")
