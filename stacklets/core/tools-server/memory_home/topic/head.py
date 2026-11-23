"""The head of a topic's page: kicker, title, facts and the key.

    head("Own topic", "Quilt for Lisa", '<span class="count-line">9 cards</span>', answer, context,
         route="managed")

The four sit close together. The key says once what ✦ beside an entry's date
means, and only when the topic has such an entry: one the AI wrote, by the
card page's rule (`memory_ui.entry.made.ai_made`, from the sidecar's `made`
the memory sends). It names the AI `route` says (`memory_ui.ai_route`), in the topic's own
words (`topic_key`), since it explains the mark for many entries at once.

Pure: no I/O.
"""

from __future__ import annotations

from html import escape

from memory_home.frame import page_head
from memory_home.items import Context
from memory_home.words import words
from memory_ui.ai_route import ROUTES
from memory_ui.entry.made import ai_made

__all__ = ["head"]


def head(kicker: str, title: str, facts: str, answer: dict, context: Context, *, route: str = "") -> str:
    """The head's HTML: `facts` is HTML, already escaped; `route` the AI's (`memory_ui.ai_route.of`)."""
    return (f'<div class="topic-head">{page_head("topic", kicker, title, facts)}'
            f'{_key(answer, context, route)}</div>')


def _key(answer: dict, context: Context, route: str) -> str:
    """The line that explains ✦; "" when the AI wrote no entry of the topic."""
    if not any(ai_made(e) for e in answer.get("entries") or []):
        return ""
    line = words(context.language)["topic_key"][route if route in ROUTES else ""]
    return f'<p class="key">{escape(line)}</p>'
