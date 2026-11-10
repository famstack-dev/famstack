"""A card's history as a timeline: who did what to it, and when.

A card page shows "History" closed. Opening it asks the server for this
fragment (`GET /card/<card id>/history`), which reads the card's
versions from the memory (`GET /cards/<id>/versions`, git) and words each
one, newest first:

    Marge   filed it                  · 9 November
    famstack  read it again           · 9 November     (quieter: the machine's)
    Homer   corrected it              · today

    html = fragment(versions, names={"marge": "Marge"}, now=now, language="en")

Why it loads only when opened: git is the slowest read on the page, and
most visits never look at the history.

Why the words come from the commit's kind (`stack.cards.commits`): every
writer of the memory starts its commit subject with a prefix from one
closed table (ADR-018 §6), so the kind ("filed", "corrected") is known
without reading the change itself. Whether a change is a person's or the
machine's is decided there too (`is_machine`); the machine's changes are
said by "famstack" and drawn quieter.

The latest three show; the rest wait behind "Show all", which the page's
script unfolds.

Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from html import escape

from memory_ui.words import plain_day, words
from stack.cards.commits import is_machine, kind_of

__all__ = ["Event", "events", "fragment", "SHOWN"]

# The latest events a closed timeline shows before "Show all".
SHOWN = 3


@dataclass(frozen=True)
class Event:
    """One change of the card: who, what in a word (a kind of
    `stack.cards.commits`), when, and whether the machine made it.

        Event("Marge", "filed", datetime(2026, 11, 9, 14, 32), machine=False)
    """

    who: str
    kind: str
    at: datetime
    machine: bool


def events(versions: list[Mapping], *, names: Mapping[str, str]) -> list[Event]:
    """The card's versions (oldest first, as the memory sends them) as
    events, newest first. `names` maps a person id to their name.

        >>> v = {"sha": "a", "at": "2026-11-09T14:32:00+00:00", "author": "marge",
        ...      "committer": "archivist-bot", "subject": "capture: Tent", "card": "", "trailers": []}
        >>> e = events([v], names={"marge": "Marge"})[0]
        >>> e.who, e.kind, e.machine
        ('Marge', 'filed', False)
    """
    found = []
    for v in versions:
        trailers = {str(k): str(val) for k, val in v.get("trailers") or ()}
        kind = kind_of(str(v.get("subject") or ""), trailers)
        author = str(v.get("author") or "")
        machine = is_machine(author, str(v.get("committer") or ""), kind)
        who = "" if machine else names.get(author.lower()) or author.capitalize()
        found.append(Event(who, kind, datetime.fromisoformat(str(v["at"])), machine))
    return found[::-1]


def fragment(versions: list[Mapping], *, names: Mapping[str, str], now: datetime,
             language: str) -> str:
    """The timeline's HTML: an ordered list, the latest `SHOWN` visible and
    the rest `hidden` behind a "Show all" button. No versions (a card
    written by hand and never committed) is an empty timeline.

        fragment([v], names={"marge": "Marge"}, now=now, language="en")
        # '<ol class="timeline"><li class="person"><b>Marge</b> filed it<time ...> · today</time></li></ol>'
    """
    w = words(language)
    found = events(versions, names=names)
    rows = [_row(e, w, now, language, hidden=i >= SHOWN) for i, e in enumerate(found)]
    more = (f'<button class="inlinebtn showall" type="button">{escape(w["show_all"])}</button>'
            if len(found) > SHOWN else "")
    return f'<ol class="timeline">{"".join(rows)}</ol>{more}'


def _row(e: Event, w: dict, now: datetime, language: str, *, hidden: bool) -> str:
    who = w["famstack"] if e.machine else e.who
    when = plain_day(e.at.astimezone(now.tzinfo).date(), today=now.date(), language=language)
    what = w["did"].get(e.kind, w["did"]["changed"])
    return (f'<li class="{"machine" if e.machine else "person"}"{" hidden" if hidden else ""}>'
            f'<b>{escape(who)}</b> {escape(what)}'
            f'<time class="when" datetime="{e.at.isoformat()}"> · {escape(when)}</time></li>')
