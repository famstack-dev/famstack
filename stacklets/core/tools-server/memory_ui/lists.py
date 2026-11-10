"""A list's page body: its sections with their items, its months in the
archive, and the line that says where items are added and ticked.

A list is a card (`list-<slug>`, `stack.lists`): one checkbox line per
item, under the headings of its sections, with the person it is assigned
to, `📅` the day it is due and `✅` the day it was ticked. The cards API
sends the items read from the file (`items`) and the list's months in
the archive (`archive`); this module turns them into the body of the
list's page, below the head that `card_types.py` draws:

    Fruit & Veg
    ▢ Apples
    ☑ Bananas  ticked by Marge                 ticked today, greyed
    Dairy
    ▢ Milk  Homer  14 November                 assigned, due
    Earlier months
    October 2026 · 24 items                    opens that month's card
    "Add or tick items in the chat: 'Stacky, we need milk.'"

    html = body(card, names={"marge": "Marge"}, today=date(2026, 11, 10), language="en")
    open_count(card)                            # 3
    coming(card, today=date(2026, 11, 10))      # ("Parents' evening", date(2026, 11, 14))

Why an item ticked before today is left out: a to-do list hands its ticked
items to the archive at night, so the page shows what the list holds
today, and the archive's months hold the rest. A checklist keeps its ticks
until it is reset, and a month of the archive is all ticked items, so
those show every item.

Why "ticked by" reads the card's history and not the file: the file says
the day an item was ticked, not who ticked it. The chat's command commits
the tick in the person's name, with a subject that names the item
(`chore(lists): marge ticked off Bananas on Groceries`). An item whose
tick no change of that day names shows ticked without a name.

What it deliberately does not do: add, tick or move an item. The page
reads only; the chat changes lists.

Pure: no I/O. `card` is the cards API's answer to `GET /card/<id>` for a
list or a month of its archive.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from html import escape

from memory_ui.addresses import card_address
from memory_ui.words import day_of, long_day, words

__all__ = ["body", "open_count", "coming"]


def body(card: Mapping, *, names: Mapping[str, str], today: date, language: str) -> str:
    """The list's body as HTML: its description, its sections in the
    list's order with the items shown today, its archive's months, and the
    chat line. A list without sections shows its items under no heading;
    a month of the archive has no months and no chat line.

    `names` maps a person id to the name a reader knows (`{"marge": "Marge"}`).

        >>> card = {"meta": {"sections": ["Dairy"]}, "items": [
        ...     {"text": "Milk", "section": "Dairy", "done": False, "people": ["homer"]}]}
        >>> html = body(card, names={"homer": "Homer"}, today=date(2026, 11, 10), language="en")
        >>> '<h2 class="section-title">Dairy</h2>' in html, '<span class="item-for">Homer</span>' in html
        (True, True)
    """
    w = words(language)
    kind = _kind(card)
    shown = [i for i in card.get("items") or [] if _shown(i, kind, today)]
    ticks = _ticks(card.get("changes") or [])
    parts = [_description(card)]
    for title, items in _sections(card, shown):
        rows = "".join(_item(i, ticks, names, today, language, w) for i in items)
        heading = f'<h2 class="section-title">{escape(title)}</h2>' if title else ""
        parts.append(f'<section class="listsec">{heading}<ul class="tasks">{rows}</ul></section>')
    if kind != _ARCHIVE:
        parts += [_months(card, w),
                  f'<blockquote class="chatline"><p>{escape(w["chat_line"])}</p></blockquote>']
    return "".join(parts)


def open_count(card: Mapping) -> int:
    """How many items of the list are open (not ticked).

        >>> open_count({"items": [{"text": "Apples"}, {"text": "Butter", "done": True}]})
        1
    """
    return sum(1 for i in card.get("items") or [] if not i.get("done"))


def coming(card: Mapping, *, today: date) -> tuple[str, date] | None:
    """A calendar list's next date: the first open item due today or
    later, with its day; None when nothing is coming.

        >>> coming({"items": [{"text": "Dentist", "due": "2026-11-03"},
        ...                   {"text": "Parents' evening", "due": "2026-11-14"}]},
        ...        today=date(2026, 11, 10))
        ("Parents' evening", datetime.date(2026, 11, 14))
    """
    found = sorted((d, str(i.get("text") or "")) for i in card.get("items") or []
                   if not i.get("done") and (d := day_of(i.get("due"))) and d >= today)
    return (found[0][1], found[0][0]) if found else None


# ── Which items show ───────────────────────────────────────────────────────

# The list kinds whose ticked items stay on the list (`stack.lists`): a
# checklist until it is reset, a month of the archive for good.
_CHECKLIST, _ARCHIVE = "checklist", "archive"


def _kind(card: Mapping) -> str:
    return str((card.get("meta") or {}).get("kind") or "todo")


def _shown(item: Mapping, kind: str, today: date) -> bool:
    """An item is on the page unless a to-do or calendar list ticked it
    before today: the night after, it moves to the archive."""
    if not item.get("done") or kind in (_CHECKLIST, _ARCHIVE):
        return True
    ticked = day_of(item.get("done_on"))
    return ticked is None or ticked >= today


def _sections(card: Mapping, items: list[Mapping]) -> list[tuple[str, list[Mapping]]]:
    """The items by section: those above the first heading first, then the
    sections in the list's order (`sections`), then any heading the list's
    order does not name, in file order. Empty sections are left out."""
    order = [""] + [str(s) for s in (card.get("meta") or {}).get("sections") or []]
    for item in items:
        if (section := str(item.get("section") or "")) not in order:
            order.append(section)
    by_section = [(s, [i for i in items if str(i.get("section") or "") == s]) for s in order]
    return [(s, found) for s, found in by_section if found]


# ── One item ───────────────────────────────────────────────────────────────

def _item(item: Mapping, ticks: Mapping[tuple[str, str], str], names: Mapping[str, str],
          today: date, language: str, w: dict) -> str:
    """One checkbox line: the box, the text, the person it is for and its
    due day; a ticked item greyed, with who ticked it when known."""
    done = bool(item.get("done"))
    text = str(item.get("text") or "")
    box = (f'<span class="box" role="img" aria-label="{escape(w["ticked" if done else "not_ticked"])}">'
           f'{"✓" if done else ""}</span>')
    parts = [box, f'<span class="item-text">{escape(text)}</span>']
    for person in item.get("people") or []:
        parts.append(f'<span class="item-for">{escape(_name(str(person), names))}</span>')
    if due := day_of(item.get("due")):
        parts.append(f'<span class="item-due">{escape(long_day(due, today=today, language=language))}</span>')
    if done and (who := ticks.get((str(item.get("done_on") or ""), text))):
        parts.append(f'<span class="by">{escape(w["ticked_by"].format(name=_name(who, names)))}</span>')
    return f'<li class="item{" done" if done else ""}">{"".join(parts)}</li>'


# `chore(lists): marge ticked off Bananas; Milk on Groceries`, as the
# chat's list command words a tick (`stacklets/memory/cli/list.py`). A
# subject that counts the items instead ("ticked off 3 items") names none.
_TICK = re.compile(r"^chore\(lists\): (?P<who>\S+) ticked off (?P<items>.+) on [^;]+$")


def _ticks(changes: list[Mapping]) -> dict[tuple[str, str], str]:
    """(day, item text) -> who ticked it, from the card's changes. The
    newest change wins, since `changes` comes newest first."""
    found: dict[tuple[str, str], str] = {}
    for change in changes:
        if m := _TICK.match(str(change.get("subject") or "")):
            for text in m["items"].split("; "):
                found.setdefault((str(change.get("day") or ""), text), m["who"].lstrip("@").split(":")[0])
    return found


def _name(person: str, names: Mapping[str, str]) -> str:
    return names.get(person.lower()) or (person[:1].upper() + person[1:])


# ── Around the items ───────────────────────────────────────────────────────

def _description(card: Mapping) -> str:
    text = str((card.get("meta") or {}).get("description") or "").strip()
    return f'<p class="list-description">{escape(text)}</p>' if text else ""


def _months(card: Mapping, w: dict) -> str:
    """"Earlier months": each month of the archive, newest first, with how
    many items it holds, linking that month's card."""
    rows = []
    for month in card.get("archive") or []:
        day = day_of(f"{month.get('month')}-01")
        if not day or not month.get("id"):
            continue
        count = int(month.get("items") or 0)
        label = (f'{w["months"][day.month - 1]} {day.year} · '
                 f'{w["items"][count != 1].format(n=count)}')
        rows.append(f'<li><a class="internal" href="{escape(card_address(str(month["id"])))}">'
                    f'{escape(label)}</a></li>')
    if not rows:
        return ""
    return (f'<section class="field"><h3 class="label subhead">{escape(w["earlier"])}</h3>'
            f'<ul class="archive">{"".join(rows)}</ul></section>')
