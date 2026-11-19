"""One card of the memory's overview as a line on a page, and what a line links to.

The memory's overview (`/overview/<view>`) answers every card as an item:

    {"id": "document-4c5d6e7f80", "type": "document", "title": "Receipt from Kwik-E-Mart",
     "day": "2026-11-02", "persons": ["marge", "grampa"],
     "person_pages": {"marge": "marge", "grampa": ""},
     "kind": "Receipt", "sender": "Kwik-E-Mart", "sender_page": "correspondent-kwik-e-mart"}

This module draws one item as a row: the day, the type's icon on its
colour, the title as a link to the card's page, and a quiet line under it
with the kind of document, the sender and the people, each a link to its
memory page. The page's sections (`sections.py`) put the rows together.

    context = Context(language="en", today=date(2026, 11, 18), me="marge", names={"bart": "Bart"})
    row(item, context)          # '<li class="row">...</li>'
    type_icon("document", kind="Receipt")

A row shows what the item holds and nothing more: the memory decided which
cards the device may see and which are the person's, and a row filters
nothing. A person or a sender links the page the item names for them
(`person_pages`, `sender_page`), and has no link without one: the memory
decided which page a name opens and whether the device may read it, and
a row matches no name itself. The person whose page it is is left out of
a row's people, because every row on their page is theirs.

Pure: no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from html import escape

from memory_home import addresses
from memory_home.words import words
from memory_ui.addresses import card_address
from memory_ui.frame import ICONS, icon
from memory_ui.words import day_of, plain_day

__all__ = ["Context", "row", "card_link", "type_icon", "person_name", "by_line"]

# The icon of a card type that has no icon of its own name, as the card
# pages draw it: a diary entry by its text, which is what every entry has.
TYPE_ICONS = {"diary": "diary-text"}
# Any other type without an icon is drawn as a note, as the card pages draw
# an unknown type, so no chip or line is left with an empty mark.
FALLBACK_ICON = "note"


@dataclass(frozen=True)
class Context:
    """What every line of a page needs besides its item: the page's
    `language`, the memory's `today` (from its answer), whose page it is
    (`me`, a person id, "" for any page but a person's), who looks at it
    (`looker`, the device's person, "" for a household device) and each
    person's first name (`names`, id to name).

    `me` and `looker` are one person on "My memory" and two on Lisa's page
    opened on Marge's phone: Lisa is left out of its rows, and Marge's own
    changes read "by you".

        Context(language="de", today=date(2026, 11, 18), me="lisa", looker="marge")
    """

    language: str
    today: date
    me: str = ""
    looker: str = ""
    names: dict[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, answer: dict, names: dict[str, str], *, language: str, me: str = "",
           looker: str = "") -> Context:
        """The context of a page drawn from the overview `answer`, whose
        `today` is the memory's day.

            Context.of({"today": "2026-11-18", ...}, {"marge": "Marge"}, language="en", me="marge")
        """
        return cls(language=language, today=date.fromisoformat(answer["today"]), me=me,
                   looker=looker, names=names)


def row(item: dict, context: Context, *, when: str | None = None, line: str = "") -> str:
    """`item` as a row: `when` (the day's words; the item's own day when
    None), the icon, the title linking the card's page, and the quiet
    line under it (`line` first, then the item's kind, sender and people).

        row({"id": "note-1", "type": "note", "title": "Tent", "day": "2026-11-01"}, context)
    """
    day = day_of(item.get("day"))
    when = when if when is not None else (
        plain_day(day, today=context.today, language=context.language) if day else "")
    quiet = " · ".join(part for part in (line, _facts(item, context)) if part)
    return (f'<li class="row"><span class="fl-date">{escape(when)}</span>'
            f'{type_icon(item.get("type", ""), kind=item.get("kind", ""))}'
            f'<span class="fl-main"><span class="change-line">{card_link(item)}</span>'
            + (f'<span class="fl-meta">{quiet}</span>' if quiet else "") + "</span></li>")


def card_link(item: dict, text: str = "") -> str:
    """A link to the item's card page, with its title or `text`."""
    return (f'<a class="internal" href="{escape(card_address(item["id"]))}">'
            f'{escape(text or item.get("title") or item["id"])}</a>')


def type_icon(card_type: str, *, kind: str = "", size: int = 14) -> str:
    """The type's icon on the type's colour; a document is drawn by the
    icon of its kind when there is one (`Receipt`), else as a document."""
    if card_type == "document" and kind.lower() in ICONS:
        name = kind.lower()
    else:
        name = card_type if card_type in ICONS else TYPE_ICONS.get(card_type, FALLBACK_ICON)
    return f'<span class="ti" data-type="{escape(card_type)}">{icon(name, size)}</span>'


def person_name(person_id: str, context: Context) -> str:
    """The first name a reader knows `person_id` by; the id itself, written
    as a name, for a person without a person card."""
    return context.names.get(person_id) or person_id.capitalize()


def _facts(item: dict, context: Context) -> str:
    """The quiet line's facts, each a link where it has a page: the kind of
    document, its sender, and the people other than the page's own. The
    page's person is told by the page id the memory gives a name, never by
    the name: a name without a page is never the page's person."""
    kind = item.get("kind", "")
    sender = item.get("sender", "")
    pages = item.get("person_pages") or {}
    people = [p for p in item.get("persons", []) if not pages.get(p) or pages[p] != context.me]
    parts = [escape(kind) if kind else "",
             _linked(sender, addresses.sender(page) if (page := item.get("sender_page")) else ""),
             ", ".join(_linked(person_name(p, context), addresses.person(page) if (page := pages.get(p))
                               else "") for p in people)]
    return " · ".join(p for p in parts if p)


def _linked(text: str, href: str) -> str:
    """`text`, as a link to `href` when there is one."""
    return f'<a href="{escape(href)}">{escape(text)}</a>' if href else escape(text)


def by_line(item: dict, context: Context) -> str:
    """Who changed the item, for "What changed": "by you" for the person
    looking, else "by Homer"."""
    w = words(context.language)
    who = item.get("by", "")
    if not who:
        return ""
    return escape(w["by_you"] if who == context.looker else w["by"].format(name=person_name(who, context)))
