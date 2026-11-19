"""A sender's page: the documents from one sender.

A sender's name on a document row or a chip opens
`/memory/sender/<card id>`, drawn from the memory's overview of that
sender (`/overview/sender/<card id>`):

    title, sheet = sender_page.sheet(answer, context)

The page names the sender, says how many documents came from them and of
which kinds, and lists the documents newest first, each naming its kind,
then the people and topics they name (not the sender itself, which every
one of them names).

Pure: no I/O.
"""

from __future__ import annotations

from functools import partial
from html import escape

from memory_home import sections
from memory_home.frame import page_head
from memory_home.items import Context
from memory_home.words import counted, words

__all__ = ["sheet"]

# The sections in the order the page shows them; the cards are the documents.
ORDER = (partial(sections.cards, key="sent"), sections.links)


def sheet(answer: dict, context: Context) -> tuple[str, str]:
    """(the browser tab's title, the page's sheet) for the overview
    `answer` of one sender."""
    w = words(context.language)
    sender = answer.get("sender") or {}
    title = sender.get("title") or sender.get("id", "")
    total = (answer.get("cards") or {}).get("count", 0)
    facts = " · ".join(escape(part) for part in (counted(total, w["document_count"]),
                                                  ", ".join(sender.get("kinds", []))) if part)
    head = page_head("correspondent", w["sender"], title, f'<span class="count-line">{facts}</span>')
    shown = _without_itself(answer, sender.get("id", ""))
    body = "".join(draw(shown, context) for draw in ORDER)
    return title, f'<article class="sheet home">{head}<div class="sections">{body}</div></article>'


def _without_itself(answer: dict, sender_id: str) -> dict:
    """The answer with the sender left out of its own links: every
    document on the page names it."""
    links = answer.get("links") or {}
    senders = [s for s in links.get("senders", []) if s.get("id") != sender_id]
    return {**answer, "links": {**links, "senders": senders}}
