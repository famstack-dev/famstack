"""The states of a topic's page that say what to do next.

    from memory_home.topic import states

    states.empty(context)                       # the sentence of a topic that holds no card
    states.not_yours("en")                      # (title, sheet) of a topic the device may not read
    states.no_more("topic-quilt", "en")         # (title, sheet) of a part past a readable topic's end

A topic nothing is filed in shows its head and one sentence on how to fill
it, with no empty section under it. A topic the device may not read (another
person's own, or one that does not exist) shows the same page for both: it
says the topic is someone's own and what to do, and names neither the owner
nor the topic, so it tells nothing about another person's folder. A part
past the end of a topic the device reads says the topic has no more
entries and leads back to it: the page knows the topic is readable, because
its first part answered for the same device (`graph_pages.open_topic`).

Pure: no I/O.
"""

from __future__ import annotations

from html import escape

from memory_home import addresses
from memory_home.frame import page_head
from memory_home.items import Context
from memory_home.words import words

__all__ = ["empty", "not_yours", "no_more"]


def empty(context: Context) -> str:
    """The sentence of a topic with no card, as a section-less paragraph."""
    return f'<p class="topic-empty">{escape(words(context.language)["topic_nothing"])}</p>'


def not_yours(language: str) -> tuple[str, str]:
    """(the tab's title, the sheet) of a topic the device may not read:
    kicker, title, why and what to do, and the way back to the person's topics."""
    w = words(language)
    return w["topic_refused"], (
        f'<article class="sheet notice topic-refused">{page_head("topic", w["topic"], w["topic_refused"])}'
        f'<p class="topic-refusal">{escape(w["topic_refused_why"])}</p>'
        f'<p class="topic-refusal">{escape(w["topic_refused_do"])}</p>'
        f'<p class="actions"><a class="btn" href="{addresses.home()}">{escape(w["your_topics_link"])}</a></p>'
        f'</article>')


def no_more(topic_id: str, language: str) -> tuple[str, str]:
    """(the tab's title, the sheet) of a part past the end of a topic the
    device reads: it has no more entries, and the way back to its first part."""
    w = words(language)
    return w["topic_no_more"], (
        f'<article class="sheet notice topic-past">{page_head("topic", w["topic"], w["topic_no_more"])}'
        f'<p class="topic-past-why">{escape(w["topic_no_more_why"])}</p>'
        f'<p class="actions"><a class="btn" href="{addresses.topic(topic_id)}">{escape(w["topic_back"])}</a></p>'
        f'</article>')
