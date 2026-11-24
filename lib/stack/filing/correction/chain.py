"""chain: what a correction thread in the chat holds.

    chain = await corrections._collect_chain(room_id, event, anchor_id, envelope)
    chain.corrections   # each correction as an event, in time order
    chain.filed         # the filing as first made, from the `*.filed` envelope

The archivist follows a reply thread back to its `*.filed` message
(`ArchivistCorrections._collect_chain`). A document is corrected from
the filing and the corrections in time order (`document`). A capture
reads the corrections as one joined hint.
"""

from __future__ import annotations

from dataclasses import dataclass

from stack.cards.events import Correction

__all__ = ["CorrectionChain"]


@dataclass(frozen=True)
class CorrectionChain:
    """What a correction thread holds (`ArchivistCorrections._collect_chain`).

    ``hint`` is every correction as one text, newest first. A capture
    reads it. ``latest`` is the classification that the person saw when
    they wrote the newest correction. ``filed`` is the filing as first
    made, or None when the thread does not reach it. ``corrections`` are
    the corrections as events in time order
    (`stack.cards.events.Correction`). A document is corrected from them.

        CorrectionChain("It is Marge's", {"title": "Car"}, {"title": "Car"},
                        (Correction("marge", "", "It is Marge's"),))
    """

    hint: str
    latest: dict | None
    filed: dict | None
    corrections: tuple[Correction, ...]
