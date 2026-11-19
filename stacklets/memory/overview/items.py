"""One card as a line on a page of the memory: the overview's item.

    item(card)
    # {"id": "document-receipt", "type": "document", "title": "Groceries receipt",
    #  "day": "2026-09-05", "persons": ["Mom", "grampa"],
    #  "person_pages": {"Mom": "marge", "grampa": ""},
    #  "kind": "Receipt", "sender": "Kwik-E-Mart", "sender_page": "correspondent-kwik-e-mart"}
    listed(cards, limit=8)       # {"count": 31, "items": [the first 8]}

Every section that names a card names it this way, so a page links any line
to the card viewer (`/card/<id>`) and draws its icon from `type` without
asking again. The fields a page shows beside the title come with it: the
kind and sender of a document, the address of a bookmark.

**Where a name leads is the graph's answer, never the page's.** Each name
in `persons` comes with the id of the person's page (`person_pages`), and
a document's sender with its card's id (`sender_page`), as the graph
resolved them when it linked the card (`Card.person_pages`,
`Card.sender_card`): an alias opens its person, and a person whose card
the audience may not read opens nothing (""). A page links a name only
by these ids, so it opens what the graph links.
"""

from __future__ import annotations

from ..graph import Card
from .scope import document_kind, sender_name

__all__ = ["item", "listed"]


def item(card: Card, **extra: object) -> dict:
    """`card` as an item: `id`, `type`, `title`, `day`, `persons` with
    their `person_pages`, the fields its type shows, then `extra`.

    A document adds `kind`, `sender` and `sender_page`, a bookmark
    `resource`. A field the card does not have is left out, never "".
    """
    out: dict = {"id": card.id, "type": card.type, "title": card.title, "day": card.day,
                 "persons": list(card.persons), "person_pages": dict(card.person_pages)}
    if card.type == "document":
        out |= _present(kind=document_kind(card), sender=sender_name(card),
                        sender_page=card.sender_card)
    if card.type == "bookmark":
        out |= _present(resource=str(card.meta.get("resource") or ""))
    return out | extra


def listed(cards: list[Card], limit: int) -> dict:
    """A part of a page that lists cards: how many there are, and the first
    `limit` of them as items, in the order given."""
    return {"count": len(cards), "items": [item(c) for c in cards[:limit]]}


def _present(**fields: str) -> dict:
    return {k: v for k, v in fields.items() if v}
