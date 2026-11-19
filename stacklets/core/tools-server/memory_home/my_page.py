"""My memory: the page of the person on the paired device.

The memory's landing page is "relevant for me": the device's pairing says
who is looking, and the page opens on that person, from the memory's
overview of them (`/overview/me`, or `/overview/person/<admin>` on the
Mac's address):

    sheet = my_page.sheet(answer, context, name="Marge")

The page greets the person, says how many cards are theirs or name them
with a link to the family's page, shows those cards by type, and then the
sections, in this order: Up next, Lists, Your topics, What
changed, Your bookmarks, Documents, and the people, topics and senders the
cards name. A section the answer does not hold (the admin's page has no
topics or bookmarks) is left out; a section with nothing in it says so.

Pure: no I/O.
"""

from __future__ import annotations

from html import escape

from memory_home import addresses, sections
from memory_home.frame import HEADING_ID
from memory_home.items import Context, type_icon
from memory_home.words import counted, words

__all__ = ["sheet"]

# The sections in the order the page shows them, each drawn when the
# overview's answer holds its part.
ORDER = (("up_next", sections.up_next), ("lists", sections.lists), ("topics", sections.topics),
         ("changed", sections.changed), ("bookmarks", sections.bookmarks),
         ("documents", sections.documents), ("links", sections.links))


def sheet(answer: dict, context: Context, *, name: str) -> str:
    """The page's sheet for the overview `answer` of the person called `name`.

        sheet({"view": "me", "counts": {"note": 4}, "lists": [], ...}, context, name="Marge")
    """
    w = words(context.language)
    mine = answer.get("total", 0)
    head = (f'<header class="family-head"><p class="kicker" data-type="family">{type_icon("family")}'
            f'{escape(w["your_memory"])}</p>'
            f'<h1 class="title" id="{HEADING_ID}" tabindex="-1">{escape(w["hello"].format(name=name))}</h1>'
            f'<p class="facts"><span class="count-line">{escape(counted(mine, w["mine"]))}</span>'
            f' · <a href="{addresses.family()}">{escape(w["whole_family"])}</a></p></header>')
    body = "".join(draw(answer, context) for part, draw in ORDER if part in answer)
    return (f'<article class="sheet home">{head}{sections.type_chips(answer, context)}'
            f'<div class="sections">{body}</div></article>')
