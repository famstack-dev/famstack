"""A topic's page: the cards of one topic, its people and its senders.

A topic tile, a menu link or a chip opens `/memory/topic/<card id>`, drawn
from the memory's overview of that topic (`/overview/topic/<card id>`):

    title, sheet = topic_page.sheet(answer, context, about=1)

The page names the topic and whose it is (the family's, the person's
looking, or another person's, by the memory's `owner`), says how many cards it holds and how many of those are about the
person looking, and then the sections: Up next, its cards newest first,
and the people and senders its cards name.

How many cards are about the person looking is the memory's count too: it
comes from that person's own overview (`topics[].about`), which the page
reads for its menu anyway, so nothing here counts.

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
ORDER = (sections.up_next, sections.cards, sections.links)


def sheet(answer: dict, context: Context, *, about: int | None) -> tuple[str, str]:
    """(the browser tab's title, the page's sheet) for the overview
    `answer` of one topic. `about` is how many of its cards are about the
    person looking, None for a device that is no person's."""
    w = words(context.language)
    topic = answer.get("topic") or {}
    title = topic.get("title") or topic.get("id", "")
    total = (answer.get("cards") or {}).get("count", 0)
    facts = f'<span class="count-line">{escape(counted(total, w["cards"]))}</span>'
    if about is not None:
        facts += f' · <span class="about-line">{escape(w["about_you"].format(n=about))}</span>'
    head = page_head("topic", _whose(topic.get("owner", ""), context), title, facts)
    shown = _without_itself(answer, topic.get("id", ""))
    body = "".join(draw(shown, context) for draw in ORDER)
    return title, (f'<article class="sheet home">{head}{sections.type_chips(answer, context)}'
                   f'<div class="sections">{body}</div></article>')


def _whose(owner: str, context: Context) -> str:
    """Whose the topic is, as its kicker says: the family's (no owner),
    the person looking's own, or another person's ("Lisa's topic", on the
    Mac's address, which reads every folder)."""
    w = words(context.language)
    if not owner:
        return w["topic_family"]
    return w["topic_own"] if owner == context.looker else w["topic_of"].format(name=person_name(owner, context))


def _without_itself(answer: dict, topic_id: str) -> dict:
    """The answer with the topic left out of its own links: every card of
    the topic names it, so it would always top the list."""
    links = answer.get("links") or {}
    topics = [t for t in links.get("topics", []) if t.get("id") != topic_id]
    return {**answer, "links": {**links, "topics": topics}}
