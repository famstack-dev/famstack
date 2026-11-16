"""`stack memory diary --preview n`: what the current prompts make of the newest cards.

    rc = await preview_cards(newest, llm, cache, on_file=on_file, bucket="family", ...)

The preview reads the newest entries as a compile would and compares
each card it makes with the card on file. It prints the comparison as
JSON for the host to render and writes nothing but the reading caches.

Debt (message groups): `--preview n` picks the room's newest part by
message groups without the compile's own rules (a named day, a late
queue, cards on file), because it runs before the model reads the room
and before the cards are read. The part it picks can then hold another
number of entries than `n`. It writes nothing. Trigger: a preview that
shows the wrong entries. Then read the cards first and pick by the
compiled entries.
"""

from __future__ import annotations

import json

from stack.message_groups import group_messages

from ...diary.card import card_path, edited_by_hand
from ...diary.plan import cards_on_file
from ...diary.plan import plan as plan_files
from .cards import cards_for, standing_cards
from .household_files import household_language_code, household_ontology, topic_labels
from .reading import OVERLAP
from .store import cards_in

# ── A preview ─────────────────────────────────────────────────────────
#
# What new prompts do to the cards, shown on a few entries before a
# compile applies them. The preview reads the newest entries the way the
# compile would and compares each card it makes with the card on file.
# It writes nothing; the readings it pays for are kept, so the compile
# that follows does not pay for them again.

# Entries read before the ones a preview shows, so an entry at the edge
# still has the messages sent before it in one go. Their cards are not shown.
_PREVIEW_RUN_UP = OVERLAP


def newest_entries(messages, count: int, *, window_s: float) -> list:
    """The newest part of the room, holding its `count` newest entries.

    Counted in message groups, the unit of one entry
    (`stack.message_groups`), with `window_s` as the compile groups:
    seven photos sent in one go are one entry, not seven. A reply in the
    part whose entry is older than the part is left out: on its own it
    would read as an entry of its own. The archivist's own posts are
    already gone (`resolve`).
    """
    groups = group_messages(messages, window_s=window_s)
    take = count + _PREVIEW_RUN_UP
    if len(groups) <= take:
        return list(messages)
    inside = {m.event_id for g in groups[-take:] for m in (*g.messages, *g.replies)}
    return [m for m in messages if m.event_id in inside]


async def preview_cards(entries, llm, extractions_cache, *, on_file: "dict[str, str]",
                   bucket: str, room_id: str, media: "dict[str, str]") -> int:
    """Print each entry's card as it is on file (`on_file`, the diary
    tree) and as a rebuild makes it now."""
    cards = await cards_for(entries, llm, extractions_cache, bucket=bucket,
                             media=media, on_file=on_file, rebuild=True, force=False)
    # A rebuild's card has the sources of the entry, so it says what a
    # compile keeps as well as a card read without the model would.
    compile_keeps = set(standing_cards(cards, on_file, rebuild=False))
    print(json.dumps(preview_report(cards, on_file, compile_keeps, room_id=room_id,
                                     bucket=bucket, ontology=household_ontology(),
                                     language=household_language_code()), ensure_ascii=False))
    return 0


def preview_report(cards, on_file: "dict[str, str]", compile_keeps: "set[str]", *,
                    room_id: str, bucket: str, ontology, language: str) -> dict:
    """Each card as it is on file (`before`, None for a new entry) and as
    the current prompts make it (`after`), with what a rebuild does to it
    (`state`: `new`, `changed`, `unchanged`, or `kept` for a card a person
    changed) and whether the next compile writes it (`next_compile`):
    a compile rewrites a card only when its entry changed. Topics show as
    the household reads them (`topic_labels`)."""
    plan = plan_files(on_file, cards, bucket=bucket, complete=False, rebuild=True)
    by_entry = cards_on_file(on_file)
    out = []
    for card in cards:
        path = card_path(card, bucket=bucket)
        old_path, before = by_entry.get(card.entry_id, ("", None))
        if path in plan.writes:
            state, after = ("changed" if before else "new"), cards_in(plan.writes)[path]
        elif before is not None and edited_by_hand(on_file[old_path]):
            state, after = "kept", before
        else:
            state, after = "unchanged", before or card
        out.append({"entry_id": card.entry_id, "path": path, "state": state,
                    "next_compile": "keeps" if card.entry_id in compile_keeps else "writes",
                    "before": _readable(before, ontology, language) if before else None,
                    "after": _readable(after, ontology, language)})
    return {"room_id": room_id, "entries": out}


def _readable(card, ontology, language: str) -> dict:
    """The parts of a card a person reads."""
    return {"date": card.on.isoformat(), "title": card.title,
            "description": card.description, "people": card.persons,
            "topics": topic_labels(card.tags, ontology, language),
            "summary": card.summary, "facts": card.facts, "quotes": card.quotes,
            "replies": [f"{who}: {text}" for who, text in card.replies],
            "words": card.body}
