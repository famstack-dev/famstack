"""Card ids: the one value that names a card for its whole life.

A card is one file in the family memory that stands for one thing. Its
path is not a name: the folder follows the bucket and the topic, the
file name often follows the title, and all of those change under
ordinary use. Each card type already carries a field that never
changes, under a name of its own, and that field is the card's id.

This module holds the part of that table the link resolver needs: the
cards it finds by reading the vault. Documents are not here because a
document link opens Paperless, which is keyed by `paperless_id` already;
people, topics and lists are linked by their names. The full table,
every type included, is in `docs/stack-reference.md`.

Stdlib only and pure: the host CLI, the bot-runner and core's tools
server all import it, so emitter and resolver read one table.
"""

from __future__ import annotations

from typing import Mapping


# ── The table ─────────────────────────────────────────────────────────
#
# Keyed by the link kind (`/go/<kind>/<id>`), valued by the frontmatter
# field that holds the id. `capture` covers notes and bookmarks: both
# carry the event id of the message they were filed from.

CARD_ID_FIELDS: dict[str, str] = {
    "capture": "capture_id",
    "diary": "entry_id",
}


def card_id_of(fields: Mapping[str, object]) -> tuple[str, str] | None:
    """(kind, card id) for a card's frontmatter, or None.

    None for a page the resolver does not find by id: a topic, a person,
    a list, a document, or a card whose id field is empty.

    >>> card_id_of({"type": "note", "capture_id": "$abc"})
    ('capture', '$abc')
    >>> card_id_of({"type": "diary", "entry_id": "3f9a0c1b2d"})
    ('diary', '3f9a0c1b2d')
    >>> card_id_of({"type": "list", "title": "Einkaufsliste"}) is None
    True
    """
    for kind, field in CARD_ID_FIELDS.items():
        value = str(fields.get(field) or "").strip()
        if value:
            return kind, value
    return None
