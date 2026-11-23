"""The states of a topic's page that say what to do next.

    from memory_home.topic import states

    states.empty(context)                       # "" unless the topic holds no card
    states.not_yours(context, menu)             # the page of a topic the device may not read

A topic nothing is filed in shows its head and one sentence on how to fill
it, with no empty section under it. A topic the device may not read (another
person's own, or one that does not exist) shows the same page for both: it
says the topic is someone's own and what to do, and names neither the owner
nor the topic, so it tells nothing about another person's folder.

Pure: no I/O.
"""

from __future__ import annotations

from html import escape

from memory_home import addresses
from memory_home.frame import page_head
from memory_home.items import Context
from memory_home.words import words

__all__ = ["empty", "not_yours"]


def empty(context: Context) -> str:
    """The sentence of a topic with no card, as a section-less paragraph."""
    return f'<p class="topic-empty">{escape(words(context.language)["topic_nothing"])}</p>'


def not_yours(language: str) -> str:
    """The sheet of a topic the device may not read: kicker, title, why and
    what to do, and the way back to the person's topics."""
    w = words(language)
    return (f'<article class="sheet notice topic-refused">{page_head("topic", w["topic"], w["topic_refused"])}'
            f'<p class="topic-refusal">{escape(w["topic_refused_why"])}</p>'
            f'<p class="topic-refusal">{escape(w["topic_refused_do"])}</p>'
            f'<p class="actions"><a class="btn" href="{addresses.home()}">{escape(w["your_topics_link"])}</a></p>'
            f'</article>')
