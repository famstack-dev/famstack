"""The family's page: the household's memory, with the sections of "My memory".

The memory's overview of the family (`/overview/family`) holds everything
the device may read: on a person's phone the family's cards and their own,
on a household device the family's, on the Mac's address every card.

    sheet = family_page.sheet(answer, context, family="The Simpsons")

The page names the family and its people, each a link to their memory
page, says how many cards it shows, and then the sections: Up next, Lists,
What changed, Documents, and the people, topics and senders the cards name.

Pure: no I/O.
"""

from __future__ import annotations

from html import escape

from memory_home import addresses, sections
from memory_home.frame import HEADING_ID
from memory_home.items import Context, person_name, type_icon
from memory_home.words import counted, words

__all__ = ["sheet"]

# The sections in the order the page shows them.
ORDER = (sections.up_next, sections.lists, sections.changed, sections.documents, sections.links)


def sheet(answer: dict, context: Context, *, family: str) -> str:
    """The page's sheet for the family's overview `answer`; `family` is
    the heading ("The Simpsons", "The family" without a name)."""
    w = words(context.language)
    people = ", ".join(
        f'<a href="{escape(addresses.person(p["id"]))}">{escape(person_name(p["id"], context))}</a>'
        for p in answer.get("people", []))
    total = answer.get("total", 0)
    facts = " · ".join(part for part in (people, f'<span class="count-line">{escape(counted(total, w["cards"]))}</span>')
                       if part)
    head = (f'<header class="family-head"><p class="kicker" data-type="family">{type_icon("family")}'
            f'{escape(w["the_family"])}</p>'
            f'<h1 class="title" id="{HEADING_ID}" tabindex="-1">{escape(family)}</h1>'
            f'<p class="facts">{facts}</p></header>')
    body = "".join(draw(answer, context) for draw in ORDER)
    return (f'<article class="sheet home">{head}{sections.type_chips(answer, context)}'
            f'<div class="sections">{body}</div></article>')
