"""card_glance: a filed card as the chat shows it, read at a glance.

The archivist answers every filing of a note, a bookmark, an email or a
voice memo with the card it wrote, and every correction with the card as
it is now. In a chat that answer has to be read in a few seconds, so it is
not the whole card (the card browser and the agent show that,
`stack.card_render`) but its glance:

    📧 [Class 4b ski week in Lindenberg](https://home.example/go/email-b61d7a3e90)

    👤 Bart, Marge · 📅 Thu 5 Nov

    > Class 4b goes on its ski week to Lindenberg from 18 to 22 January. …

    - [ ] Sign the consent form 📅 Fri 20 Nov
    - [ ] Transfer EUR 200 to the trip account 📅 Mon 30 Nov
    - [ ] Decide about the ski rental 📅 Fri 20 Nov
    - and 3 more

    🏷️ school · ski-week · class-4b · forms +4

    glance = card_glance(t, "email", classification, link=link,
                         captured_at="2026-11-05", language="en")

Why it looks like this (the layout was chosen on the rig in seven rounds,
2026-11-05, card "show a filed card in chat as a short, linked card"):

- The icon of the card's type opens the first line, and the title is the
  link to the card. "Captured:" and a ✅ said nothing the reaction on the
  message does not already say, and a bare URL under the card got a link
  preview that repeated the card.
- One line names the people and the day; the tags close the card, because
  they matter least when reading and most when checking a correction.
- Limits keep a long email to about a dozen lines: the summary's first
  paragraph up to about 280 characters, 3 to-dos in the classifier's
  order (it lists what matters first; sorting by date hid a payment), and
  4 tags. The card behind the link has the rest. A correction's card
  shows every tag (`all_tags`), so the person can check what changed.
- No list of facts: they repeated the summary.

Pure: no Matrix, no memory. The reply presenter (`reply_presenter`)
wraps it in the reply's own lines; the archivist sends it.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Callable

from stack.card_render import render
from stack.lists import action_item

__all__ = ["card_glance", "TYPE_ICONS"]

# `t(key, **kwargs) -> str`: the archivist's translation lookup.
Translator = Callable[..., str]

# The icon that opens a card's first line, by card type. A voice memo is
# filed as a note, and its "🎤 What I heard" quote already says it was spoken.
TYPE_ICONS = {"email": "📧", "note": "📝", "bookmark": "🔖", "document": "📄"}
_DEFAULT_ICON = TYPE_ICONS["note"]

# About two short sentences: what a person reads before deciding to open the card.
SUMMARY_CHARS = 280
TODOS_SHOWN = 3
TAGS_SHOWN = 4

_PEOPLE, _DAY, _TAGS, _TODO_DUE = "👤", "📅", "🏷️", "📅"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def card_glance(
    t: Translator,
    card_type: str,
    classification: dict,
    *,
    title: str,
    link: str,
    captured_at: str | None,
    language: str = "en",
    all_tags: bool = False,
    today: _dt.date | None = None,
) -> str:
    """The card as markdown for a chat reply: title line, people and day, summary, to-dos, tags.

    `classification` holds what the card says: `persons`, `summary`,
    `action_items` and the `tags` to show. `title` links to `link` (the
    card's `/go` address); without a link the title stands alone.
    `captured_at` is the card's day (`YYYY-MM-DD`), shown in `language`
    with the year only when it is not `today`'s. Empty parts drop out.

        >>> t = lambda key, **kw: {"todos_more": "and {count} more"}[key].format(**kw)
        >>> print(card_glance(t, "note", {"persons": ["Homer"], "tags": ["car"]},
        ...                   title="Winter tyres", link="https://h/go/note-1",
        ...                   captured_at="2026-11-05", today=_dt.date(2026, 11, 5)))
        📝 [Winter tyres](https://h/go/note-1)
        <BLANKLINE>
        👤 Homer · 📅 Thu 5 Nov
        <BLANKLINE>
        🏷️ car
    """
    icon = TYPE_ICONS.get(card_type, _DEFAULT_ICON)
    heading = f"{icon} [{title}]({link})" if link else f"{icon} {title}"
    today = today or _dt.date.today()
    return _joined([
        heading,
        _people_and_day(classification.get("persons"), captured_at, language, today),
        _summary_quote(classification.get("summary")),
        _todo_lines(t, classification.get("action_items"), language, today),
        _tag_line(classification.get("tags"), all_tags=all_tags),
    ])


# ── The line of people and the day ───────────────────────────────────────


def _people_and_day(persons, captured_at: str | None, language: str, today: _dt.date) -> str:
    """`👤 Bart, Marge · 📅 Thu 5 Nov`, either half dropping out when unknown.

    `persons` are ids (`bart`) or names; they are named as every surface
    names them (`stack.card_render`), an id nobody declared capitalised.
    """
    ids = [persons] if isinstance(persons, str) else persons or []
    ids = [n.strip() for n in ids if isinstance(n, str) and n.strip()]
    names = render({"type": "note", "persons": ids}, "", language=language).facts if ids else ""
    day = _day_in_words(captured_at, language, today)
    parts = [f"{_PEOPLE} {names}" if names else "",
             f"{_DAY} {day}" if day else ""]
    return " · ".join(p for p in parts if p)


# Short day and month names; the household language is English or German.
_WEEKDAYS = {"en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
             "de": ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")}
_MONTHS = {"en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
           "de": ("Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
                  "Jul", "Aug", "Sep", "Okt", "Nov", "Dez")}


def _day_in_words(iso_day: str | None, language: str, today: _dt.date) -> str:
    """`2026-11-05` as `Thu 5 Nov` (German: `Do 5. Nov`); "" for no day or a malformed one.

    The year is added when it is not this year's: `Fri 8 Jan 2027`.
    """
    try:
        day = _dt.date.fromisoformat(str(iso_day or "")[:10])
    except ValueError:
        return ""
    lang = "de" if language.lower().startswith("de") else "en"
    dot = "." if lang == "de" else ""
    words = f"{_WEEKDAYS[lang][day.weekday()]} {day.day}{dot} {_MONTHS[lang][day.month - 1]}"
    return words if day.year == today.year else f"{words} {day.year}"


# ── The summary ──────────────────────────────────────────────────────────


def _summary_quote(summary) -> str:
    """The summary's first paragraph, cut to about `SUMMARY_CHARS`, as one quote.

    Whole sentences are kept while they fit; a first sentence longer than
    the limit is cut at a word. "…" marks that the card says more.
    """
    if not isinstance(summary, str) or not summary.strip():
        return ""
    paragraphs = [p for p in re.split(r"\n\s*\n", summary.strip()) if p.strip()]
    first = " ".join(paragraphs[0].split())
    shown = _within_limit(first)
    cut = shown != first or len(paragraphs) > 1
    return f"> {shown} …" if cut else f"> {shown}"


def _within_limit(text: str) -> str:
    if len(text) <= SUMMARY_CHARS:
        return text
    kept = ""
    for sentence in _SENTENCE_END.split(text):
        if len(kept) + len(sentence) + 1 > SUMMARY_CHARS:
            break
        kept = f"{kept} {sentence}".strip()
    return kept or text[:SUMMARY_CHARS].rsplit(" ", 1)[0]


# ── The to-dos ───────────────────────────────────────────────────────────


def _todo_lines(t: Translator, items, language: str, today: _dt.date) -> str:
    """The first `TODOS_SHOWN` to-dos as open boxes with their day, then "and N more"."""
    todos = [item for ai in (items if isinstance(items, list) else [])
             if (item := action_item(ai)) is not None]
    if not todos:
        return ""
    lines = []
    for item in todos[:TODOS_SHOWN]:
        due = _day_in_words(item.due, language, today) if item.due else ""
        lines.append(f"- [ ] {item.text}" + (f" {_TODO_DUE} {due}" if due else ""))
    if len(todos) > TODOS_SHOWN:
        lines.append(f"- {t('todos_more', count=len(todos) - TODOS_SHOWN)}")
    return "\n".join(lines)


# ── The tags ─────────────────────────────────────────────────────────────


def _tag_line(tags, *, all_tags: bool) -> str:
    """`🏷️ school · ski-week · class-4b · forms +4`; every tag when `all_tags`."""
    shown = [x.strip() for x in tags if isinstance(x, str) and x.strip()] \
        if isinstance(tags, list) else []
    if not shown:
        return ""
    if all_tags or len(shown) <= TAGS_SHOWN:
        return f"{_TAGS} " + " · ".join(shown)
    return f"{_TAGS} " + " · ".join(shown[:TAGS_SHOWN]) + f" +{len(shown) - TAGS_SHOWN}"


def _joined(blocks: list[str]) -> str:
    return "\n\n".join(b for b in blocks if b and b.strip())
