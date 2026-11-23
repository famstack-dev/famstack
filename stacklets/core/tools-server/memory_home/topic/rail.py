"""The rail of a topic's page: its lists, then what changed in it lately.

    rail(answer, context)    # <aside class="topic-rail">Lists 2 · Recent 3</aside>

The two sections answer "what is still to do?" and "what moved since my last
visit?" without reading every entry. The page puts the rail before its other
sections, so a phone shows it after the chips; from 1100 px the stylesheet
(`static/topic.css`) moves it to a column beside them.

- **Lists.** A list the topic's tag names, a to-do list, shows its title,
  "4 open" and its open items in the look of a list's items (`.tasks`). The
  memory sends at most its first few open items. Any other list shows its
  name as a link and its open count, because it is read when it is needed.
  On a phone a to-do list shows its first `PHONE_ITEMS` items and "All 4 ›",
  a link to the list: the page marks the items past them (`rail-more`) and
  the stylesheet hides them below the rail's width.
- **Recent.** What was filed or changed in the last two weeks, newest first,
  as the person page's "What changed": the card's icon, its title, then the
  day, what happened and who. The first `RECENT_SHOWN` rows, and the heading
  counts them all. A reply added to an entry reads "Added to <title>".

The words and counts are the memory's own: the answer's `lists[].open`,
`changed.count` and each change's `what` (the kind of the commit). Nothing
is counted or decided here.

Pure: no I/O. `answer` is the memory's overview of the topic.
"""

from __future__ import annotations

from html import escape

from memory_home import sections
from memory_home.items import Context, by_line, card_link, type_icon
from memory_home.words import counted, words
from memory_ui.addresses import card_address
from memory_ui.words import day_of, plain_day

__all__ = ["rail"]

# How many open items of a to-do list a phone shows before "All 4 ›".
PHONE_ITEMS = 3
# How many rows Recent shows; its heading counts them all.
RECENT_SHOWN = 8


def rail(answer: dict, context: Context) -> str:
    """The rail's HTML; always there, because each section says in a line when it has nothing."""
    return f'<aside class="topic-rail">{_lists(answer, context)}{_recent(answer, context)}</aside>'


# ── Lists ─────────────────────────────────────────────────────────────────

def _lists(answer: dict, context: Context) -> str:
    # The to-do list first (it has items), then the others, each group by title as the memory sent them.
    found = sorted(answer.get("lists") or [], key=lambda lst: not lst.get("items"))
    body = "".join(_todo(lst, context) if lst.get("items") else _other(lst) for lst in found)
    return sections.section("lists", context, body, len(found) or None)


def _todo(lst: dict, context: Context) -> str:
    """A to-do list: its title and open count, its open items, and "All n ›" when a
    narrower screen leaves some out. The link also stays where the memory sent
    fewer items than are open, so a long list leads to its own page."""
    w = words(context.language)
    items = lst["items"]
    href = escape(card_address(lst["id"]))
    rows = "".join(_item(i, n, context) for n, i in enumerate(items))
    all_wide = lst.get("open", 0) > len(items)
    all_link = (f'<a class="all-items internal{"" if all_wide else " phone-only"}" href="{href}">'
                f'{escape(w["all_n"].format(n=lst.get("open", len(items))))}</a>'
                if all_wide or len(items) > PHONE_ITEMS else "")
    return (f'<div class="todo-list"><p class="todo-title">{type_icon("list", size=12)}'
            f'<a class="internal" href="{href}">{escape(lst.get("title", ""))}</a>'
            f'<span class="fl-count">{escape(counted(lst.get("open", len(items)), w["open_n"]))}</span></p>'
            f'<ul class="tasks">{rows}</ul>{all_link}</div>')


def _item(item: dict, position: int, context: Context) -> str:
    """One open item: the box, its text and its due day."""
    day = day_of(item.get("day"))
    due = (f'<span class="item-due">{escape(plain_day(day, today=context.today, language=context.language))}</span>'
           if day else "")
    more = ' class="rail-more"' if position >= PHONE_ITEMS else ""
    return f'<li{more}><span class="box" aria-hidden="true"></span><span class="item-text">{escape(item.get("title", ""))}</span>{due}</li>'


def _other(lst: dict) -> str:
    """A list that is not the topic's to-do list: its name, a link, and its open count."""
    return (f'<p class="rail-list">{type_icon("list", size=12)}'
            f'<a class="internal" href="{escape(card_address(lst["id"]))}">{escape(lst.get("title", ""))}</a>'
            f'<span class="fl-count">{lst.get("open", 0)}</span></p>')


# ── Recent ────────────────────────────────────────────────────────────────

def _recent(answer: dict, context: Context) -> str:
    part = answer.get("changed") or {}
    items = part.get("items", [])[:RECENT_SHOWN]
    rows = "".join(_change(i, context) for i in items)
    return sections.section("recent", context, f'<ul class="fl-rows">{rows}</ul>' if items else "",
                            part.get("count") or None)


def _change(item: dict, context: Context) -> str:
    """One change: the card's icon and title, then "Mon 16 Nov · New entry · by you".
    A reply reads "Added to <title>" and has no separate "what"."""
    w = words(context.language)
    day = day_of(item.get("on"))
    when = plain_day(day, today=context.today, language=context.language) if day else ""
    replied = item.get("what") == "replied"
    title = (escape(w["added_to"].format(title="\0")).replace("\0", card_link(item)) if replied
             else card_link(item))
    quiet = " · ".join(part for part in (escape(when), "" if replied else escape(_what(item, context)),
                                         by_line(item, context)) if part)
    return (f'<li class="row">{type_icon(item.get("type", ""), kind=item.get("kind", ""))}'
            f'<span class="fl-main"><span class="change-line">{title}</span>'
            f'<span class="fl-meta">{quiet}</span></span></li>')


def _what(item: dict, context: Context) -> str:
    """What happened to the card: "New entry" for a filing, the kind's word for the rest."""
    w = words(context.language)
    what = item.get("what", "")
    if what == "filed":
        return w["new_card"].get(item.get("type", ""), w["new_card"][""])
    return w["changed_how"].get(what, w["changed_how"]["changed"])
