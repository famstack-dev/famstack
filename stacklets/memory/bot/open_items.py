"""The open items of the family's lists, as a topic page shows them.

A list is a file of its own (`<bucket>/lists/<slug>.md`, format in
`stack.lists`) that names its topics and links its items to people. The
wiki shows them where the family already looks: a topic's page lists the
open items of every list naming the topic, under the list's name.

The items are copied as they are, never through the model, and a done
item is never shown: the page says what is still to do. A due day reads
`📅 16 Oct 2026`, the marker the list files use, in the household's
date format.

    lists = [("family/lists/trip.md", read_list(text, path="family/lists/trip.md"))]
    topic_items(lists, "trip", "en")
"""

from __future__ import annotations

from page_template import format_date
from stack.lists import FamilyList, Item

Lists = list[tuple[str, FamilyList]]


def topic_items(lists: Lists, topic: str, lang: str) -> str:
    """The open items of the lists naming `topic`, each list under its linked name."""
    groups = []
    for path, lst in lists:
        if topic not in lst.topics or not lst.open_items:
            continue
        lines = [_line(item, lang, _people(item)) for item in lst.open_items]
        groups.append("\n".join([f"**{_link(path, lst)}**", *lines]))
    return "\n\n".join(groups)


def topic_texts(lists: Lists, topic: str) -> list[str]:
    """The open items' texts for `topic`, for a prompt that only needs to know them."""
    return [item.text for _, lst in lists if topic in lst.topics for item in lst.open_items]


def _line(item: Item, lang: str, names: list[str] | None = None) -> str:
    """`- [ ] Book the lodge · Homer · 📅 16 Oct 2026`, with the `names` given."""
    bits = list(names or [])
    if item.due:
        bits.append(f"📅 {format_date(item.due, lang)}")
    return " · ".join([f"- [ ] {item.text}", *bits])


def _people(item: Item) -> list[str]:
    """The names of the people an item is linked to, as the list writes them."""
    return [link.label for link in item.links if link.person]


def _link(path: str, lst: FamilyList) -> str:
    # Absolute from the vault root, as every wiki link (see `_relative_link`).
    return f"[{lst.title}](/{path})"
