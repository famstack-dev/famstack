"""The pages that stand in for a card: a removed card, and no memory yet.

A link in the family's chat names one card and never changes. When the
card behind it is gone, or the memory was never set up, the link still
opens a page that says so in a sentence and offers the one next step:

    ┌──────────────────────────────────────────────┐
    │ This card is gone                            │  title
    │ Marge removed it on 3 November.              │  who, and the day
    │ [ ‹ Dashboard ]                              │  to /dashboard
    └──────────────────────────────────────────────┘

    ┌──────────────────────────────────────────────┐
    │ The family memory is not set up yet          │
    │ Once it runs, every link in the chat opens   │
    │ its card here.                               │
    │ [ Set it up ]                                │  to /setup
    └──────────────────────────────────────────────┘

    html = gone("marge", date(2026, 11, 3), names={"marge": "Marge"},
                today=date(2026, 11, 10), language="en")
    html = not_set_up(language="de")

The removed card's page names no title and no commit: the removing
commit's subject can carry the card's title, and the page answers anyone
on the home network who has the link. Which status a page answers with
(410, 404) is the server's (`server.py`); when a card is removed is the
memory's history (`stack.cards.lookup.removal_of`).

Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from html import escape

from access import DASHBOARD_PREFIX, SETUP_PATH
from memory_ui.frame import document
from memory_ui.words import long_day, words

__all__ = ["gone", "not_set_up"]

# The first button leads back to the family's dashboard
# (`DASHBOARD_PREFIX`); the second to the admin's setup page (`SETUP_PATH`).


def gone(who: str, day: date, *, names: Mapping[str, str], today: date, language: str) -> str:
    """The page for a removed card: that it is gone, who removed it on
    which day, and a button back to the dashboard. `who` is the person id
    the removing commit was made as; `names` maps it to the name a reader
    knows ("marge" to "Marge"). The year shows only when it is not this year.

        gone("marge", date(2026, 11, 3), names={"marge": "Marge"},
             today=date(2026, 11, 10), language="en")
        # "This card is gone" · "Marge removed it on 3 November." · "‹ Dashboard"
    """
    w = words(language)
    name = names.get(who.lower()) or (who[:1].upper() + who[1:])
    line = w["gone_line"].format(who=name, day=long_day(day, today=today, language=language))
    return _page(w["gone_title"], line, (w["dashboard"], DASHBOARD_PREFIX), language)


def not_set_up(*, language: str) -> str:
    """The page for a card link or a card page on an instance whose memory was
    never set up, with a button to the setup page.

        not_set_up(language="en")
        # "The family memory is not set up yet" · "Once it runs, …" · "Set it up"
    """
    w = words(language)
    return _page(w["off_title"], w["off_line"], (w["off_button"], SETUP_PATH), language)


def _page(title: str, line: str, action: tuple[str, str], language: str) -> str:
    label, href = action
    sheet = (f'<article class="sheet notice"><h1 class="title">{escape(title)}</h1>'
             f'<p class="intro">{escape(line)}</p>'
             f'<p class="actions"><a class="btn primary" href="{escape(href)}">{escape(label)}</a></p>'
             "</article>")
    return document(sheet, title=title, language=language)
