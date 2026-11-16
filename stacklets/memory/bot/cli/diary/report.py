"""The JSON report of a compile, the cards the archivist posts.

    print(json.dumps(diary_report(cards, plan, tree, failed, bucket="family",
                             room_id=room_id, posted=roots, ended_by=ended_by)))

One object per card: its state (new, changed, unchanged), whether its
notice is in the room already, and the thread it goes in.
"""

from __future__ import annotations

from dataclasses import replace

from ...diary.card import card_path
from ...diary.texts import day_label
from .store import cards_in


def card_json(card, *, path: str, room_id: str, root: str = "") -> dict:
    """A card as the archivist posts it, in the thread of `root`.

    `root` is the message that ended the entry's message group, its "--"
    included; without one, the card's last own message. The notice shows
    no topics (`diary_room.card_text`): `tags` rides in the notice's event
    data as the card holds it, topic ids, like `persons`.
    """
    own = card.originals or card.sources
    return {
        "entry_id": card.entry_id, "card_id": card.card_id, "path": path,
        "root": root or (own[-1].id if own else ""), "room_id": room_id,
        "date": card.on.isoformat(), "date_basis": card.confidence,
        "date_label": day_label(card.on), "at": card.at,
        "title": card.title, "summary": card.summary,
        "facts": card.facts, "persons": card.persons,
        "tags": [t for t in card.tags if not t.startswith("Person: ")],
    }


def diary_report(cards, plan, tree, failed, *, bucket: str, room_id: str,
            posted: "set[str]", ended_by: "dict[str, str] | None" = None) -> dict:
    """What this run filed, for whoever posts the cards to the family.

    Every card of the run: `new`, `changed` or `unchanged`, and whether
    it was posted (`posted`): a card counts as posted when the thread of
    any of its messages, or of the "--" that ended it, holds a card notice
    (`posted` holds those threads' roots). So a card filed by a run nobody
    announced still gets posted, and a card filed before, whose notice is
    on its first message, is not posted again. `root` is where a notice
    goes: the message that ended the entry's group (`ended_by`, by entry
    id), else the card's last own message. A card the family owns is
    reported as they left it. Cards whose write failed are left out:
    announcing them would promise something the vault does not hold.
    """
    ended_by = ended_by or {}
    on_file = {card.entry_id: card
               for card in cards_in({p: t for p, (_, t) in tree.items()}).values()}
    out = []
    for card in cards:
        path = card_path(card, bucket=bucket)
        if path in failed:
            continue
        before = on_file.get(card.entry_id)
        if path in plan.writes:
            # The compile's card gets its id in the plan; what was written has it.
            written = cards_in(plan.writes)[path]
            state = "changed" if before is not None else "new"
            shown = replace(card, card_id=written.card_id)
        else:
            state, shown = "unchanged", (before or card)
        root = ended_by.get(card.entry_id, "")
        out.append({**card_json(shown, path=path, room_id=room_id, root=root),
                    "state": state,
                    "posted": any(i in posted for i in (root, *card.event_ids) if i)})
    return {"room_id": room_id, "cards": out,
            "new": sum(1 for c in out if c["state"] == "new"),
            "kept_as_edited": list(plan.kept)}
