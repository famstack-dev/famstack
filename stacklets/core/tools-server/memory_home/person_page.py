"""A person's page: what the memory holds about one person of the family.

From any page, a person's name links here (`/memory/person/lisa`), and the
memory's overview of that person (`/overview/person/lisa`) holds what the
device looking may read about them:

    title, sheet = person_page.sheet(answer, context)

The page names the person, says how many cards name them, shows those
cards by type, and then the sections: Up next, Lists, Topics, What
changed, Documents, the cards that name them, and the people, topics and
senders those cards name ("Often with"). It is "My memory" as seen from
another person's phone: the rows leave the page's person out, and the
tiles say "about Lisa" instead of "about you". Opened for the person
looking, it says "you", as their own page does.

Pure: no I/O.
"""

from __future__ import annotations

from html import escape

from memory_home import sections
from memory_home.frame import page_head
from memory_home.items import Context, person_name
from memory_home.words import counted, words

__all__ = ["sheet"]

# The sections in the order the page shows them.
ORDER = (sections.up_next, sections.lists, sections.topics, sections.changed, sections.documents,
         sections.cards, sections.links)


def sheet(answer: dict, context: Context) -> tuple[str, str]:
    """(the browser tab's title, the page's sheet) for the overview
    `answer` of one person (`/overview/person/<id>`); `context.me` is that
    person."""
    w = words(context.language)
    person = answer.get("person") or {}
    title = str(person.get("title") or "").strip() or person_name(context.me, context)
    total = answer.get("total", 0)
    head = page_head("person", w["person"], title,
                     f'<span class="count-line">{escape(counted(total, w["cards"]))}</span>')
    body = "".join(draw(answer, context) for draw in ORDER)
    return title, (f'<article class="sheet home">{head}{sections.type_chips(answer, context)}'
                   f'<div class="sections">{body}</div></article>')
