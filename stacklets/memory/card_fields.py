"""The fields each card type's schema names, read without a JSON Schema library.

A card type's fields are the `properties` of its schema and of the base it
refers to (`schemas/cards/`). A field the two do not name is refused on
write (`unevaluatedProperties`, `card_schema.py`) and reported by the
format check, which runs on the host as well as in the curator: so this
reads the same files with the standard library alone.

    card_fields("bookmark")                   # {"type", "card_id", "title", "capture_id", ...}
    unknown_fields({"type": "note", "card_id": "note-7f3a9c21e4", "title": "Grill",
                    "slug": "grill"})         # ["slug"]

`kind` is a field only of the types with variants (diary, list), so the
base does not name it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas" / "cards"
BASE = "base"


@lru_cache(maxsize=None)
def card_fields(card_type: str, folder: Path = SCHEMA_DIR) -> frozenset[str]:
    """The fields a card of `card_type` may hold; empty for a type no schema has."""
    if card_type == BASE or not (folder / f"{card_type}.json").is_file():
        return frozenset()
    named: dict[str, object] = {}
    for name in (BASE, card_type):
        named.update(json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))
                     .get("properties", {}))
    return frozenset(named)


def unknown_fields(fields: Mapping[str, object], folder: Path = SCHEMA_DIR) -> list[str]:
    """The keys of a card's `fields` its type's schema does not name, in their order.

    [] for a card whose `type` no schema has: that is another finding.
    """
    known = card_fields(str(fields.get("type") or ""), folder)
    return [key for key in fields if known and key not in known]
