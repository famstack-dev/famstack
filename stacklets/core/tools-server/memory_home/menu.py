"""What the memory's menu lists: the person's topics, and every kind of card with its count.

Every memory page has the same menu (`frame.py` draws it). Its two groups
come from two of the memory's views, read for the device:

    menu = await menu_of(reads, looking)
    menu.topics     [{"id": "topic-camping", "title": "Camping", "cards": 2, ...}]
    menu.kinds      [{"type": "document", "count": 2}, {"type": "note", "count": 5}]
    menu.senders    2

    menu = Menu.of(answer["topics"], await everything(reads))   # "My memory" has its topics already

- **"Your topics"** are the topics of the person the device is paired to,
  as their "My memory" answers them (`looking.own_topics`). A household
  device has none.
- **"Everything"** is each kind of card the device may read, with its
  count, and how many senders sent it documents: the memory's view
  `everything`, read with the device's own reads, so each count is the
  count the kind's page shows. On the Mac's address that is every card,
  while its topics are the admin's.

A kind the device has no card of is not in the answer, so the menu never
offers a page that answers "not found". When the memory does not answer,
the menu has no groups; the page says so itself.

The I/O edge of the menu; `frame.py` draws it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from memory_home.looking import Looking, own_topics
from memory_ui.reads import Reads

__all__ = ["Menu", "menu_of", "everything"]


@dataclass(frozen=True)
class Menu:
    """The menu's groups for one device: its person's `topics`, each kind
    of card it may read with its count (`kinds`), and how many `senders`.

        Menu.of([], {"kinds": [{"type": "note", "count": 2}], "senders": {"count": 1}})
        # Menu(topics=[], kinds=[{"type": "note", "count": 2}], senders=1)

    `Menu()` is the menu with no groups: a household device's while the
    memory does not answer.
    """

    topics: list[dict] = field(default_factory=list)
    kinds: list[dict] = field(default_factory=list)
    senders: int = 0

    @classmethod
    def of(cls, topics: list[dict] | None, everything: dict | None) -> Menu:
        """The menu from the person's topics and the memory's `everything` answer."""
        found = everything or {}
        return cls(list(topics or []), list(found.get("kinds", [])),
                   int((found.get("senders") or {}).get("count", 0)))


async def menu_of(reads: Reads, looking: Looking) -> Menu:
    """The menu of a page the device `looking` opens: its person's topics
    and everything it may read, both asked of the memory at once.

        await menu_of(reads, Looking("person", "marge"))
        # Menu(topics=[{"id": "topic-camping", ...}], kinds=[{"type": "document", "count": 2}, ...], senders=2)
    """
    topics, found = await asyncio.gather(own_topics(reads, looking), everything(reads))
    return Menu.of(topics, found)


async def everything(reads: Reads) -> dict:
    """The memory's view `everything` for the device's audience: each kind
    of card with its count and how many senders; {} when the memory does
    not answer.

        await everything(reads)
        # {"view": "everything", "kinds": [{"type": "note", "count": 2}], "senders": {"count": 1}, ...}
    """
    _, answer = await reads.overview("everything")
    return answer or {}
