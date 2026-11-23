"""The head of a topic's page: kicker, title, facts and the key.

    head("Own topic", "Quilt for Lisa", '<span class="count-line">9 cards</span>', answer, context)

The four sit close together. The key says once what ✦ beside an entry's date
means, and only when the topic has such an entry: the memory marks an entry
`made` when a model wrote its text.

Pure: no I/O.
"""

from __future__ import annotations

from html import escape

from memory_home.frame import page_head
from memory_home.items import Context
from memory_home.words import words

__all__ = ["head"]


def head(kicker: str, title: str, facts: str, answer: dict, context: Context) -> str:
    """The head's HTML: `facts` is HTML, already escaped."""
    return f'<div class="topic-head">{page_head("topic", kicker, title, facts)}{_key(answer, context)}</div>'


def _key(answer: dict, context: Context) -> str:
    """The line that explains ✦; "" when no entry of the topic was written by a model."""
    if not any(e.get("made") for e in answer.get("entries") or []):
        return ""
    return f'<p class="key">{escape(words(context.language)["topic_key"])}</p>'
