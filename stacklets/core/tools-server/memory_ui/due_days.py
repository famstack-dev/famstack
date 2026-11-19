"""The due days in a card's text, written as the memory writes days.

    due_days('<li>Pay entry fee 📅 2026-11-30</li>', today=date(2026, 11, 19), language="en")
    # '<li>Pay entry fee <span class="item-due">30 November</span></li>'

A filed letter's card carries its to-dos in its text as the lists write
them: `- Pay entry fee of 40.00 USD 📅 2026-11-30`. The mark and the ISO
day are the file's format, which the lists and Obsidian read; a person
reading the card's page reads days as the rest of the memory's pages write
them ("30 November", "2. Dezember 2027"). The list pages write an item's due
day the same way (`lists.py`), so a to-do reads alike on its letter and on
its list.

It works on the HTML `text.to_html` made of the card's text: what it
replaces is a mark and digits, which escaping leaves as they are, and what
it writes is a day it formatted itself, so nothing from the card's text
reaches the page unescaped. It changes nothing else, and a day that is no
date (`📅 2026-02-30`) stays as written.

Pure: no I/O. The card page (`card_page.py`) calls it on the card's text.
"""

from __future__ import annotations

import re
from datetime import date
from html import escape

from memory_ui.words import day_of, long_day
from stack.lists import DUE

__all__ = ["due_days"]

# The due mark and its day as the lists write them, with the space before
# it, and the variation selector some keyboards add after the emoji.
_DUE_DAY = re.compile(rf"\s*{DUE}️?\s*(\d{{4}}-\d{{2}}-\d{{2}})")


def due_days(html: str, *, today: date, language: str) -> str:
    """`html` with every `📅 <ISO day>` written as the day, in a span of
    the class `item-due`, as the list pages write a due day.

        >>> due_days("<li>Pay 📅 2026-11-30</li>", today=date(2026, 11, 19), language="de")
        '<li>Pay <span class="item-due">30. November</span></li>'
        >>> due_days("<p>Soon 📅 2026-02-30</p>", today=date(2026, 11, 19), language="en")
        '<p>Soon 📅 2026-02-30</p>'
    """
    def written(found: re.Match) -> str:
        day = day_of(found.group(1))
        if day is None:
            return found.group(0)
        return f' <span class="item-due">{escape(long_day(day, today=today, language=language))}</span>'

    return _DUE_DAY.sub(written, html)
