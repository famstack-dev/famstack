"""The links between the parts of a long list: "Newer" and "Older".

A page with a long list of cards (a person, a topic, a sender, the
documents, a kind) shows them 40 at a time, newest first. The memory answers which part a page shows and how
many there are (`{"count": 45, "items": [...], "part": 1, "parts": 2}`);
this module draws the links to the part before and after it:

    pager({"part": 2, "parts": 3, ...}, "/memory/kind/note", context)
    # '<nav class="pager" ...><a class="pager-newer" href="/memory/kind/note">Newer</a>
    #  <span class="pager-where">2 of 3</span>
    #  <a class="pager-older" href="/memory/kind/note?part=3">Older</a></nav>'

Each part has an address of its own (`addresses.in_part`), so Back, a
bookmark and a shared link each open the same cards. A list of one part
draws nothing. The links are plain links, so the paging works without
the page's script, as the rest of the page does.

Pure: no I/O.
"""

from __future__ import annotations

from html import escape

from memory_home import addresses
from memory_home.items import Context
from memory_home.words import words

__all__ = ["pager"]


def pager(part: dict, address: str, context: Context) -> str:
    """The links to the part before ("Newer") and after ("Older") the part
    `part` of the list whose first part is at `address`, and where this
    part is; "" for a list of one part, or a part with no `parts`."""
    at, parts = part.get("part", 1), part.get("parts", 1)
    if parts <= 1:
        return ""
    w = words(context.language)
    newer = _link("newer", addresses.in_part(address, at - 1), w["newer"]) if at > 1 else ""
    older = _link("older", addresses.in_part(address, at + 1), w["older"]) if at < parts else ""
    where = f'<span class="pager-where">{escape(w["part_of"].format(part=at, parts=parts))}</span>'
    return (f'<nav class="pager" aria-label="{escape(w["parts_label"])}">'
            f'{newer}{where}{older}</nav>')


def _link(direction: str, href: str, label: str) -> str:
    """One link of the pager; `rel` tells a browser which way it goes."""
    rel = "prev" if direction == "newer" else "next"
    return f'<a class="pager-{direction}" rel="{rel}" href="{escape(href)}">{escape(label)}</a>'
