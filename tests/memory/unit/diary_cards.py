"""Diary entries and cards built in a test from the fields of an entry.

A test that needs a diary card in the memory (a card a migration reads,
a card a view shows), or an entry to make one from, knows it by what the
entry was: who sent it, when, its event ids, its words, its replies and
its files. The diary keeps one source per message (ADR-018), made from
the entry's parts, the room's messages as the compile groups them. These
helpers give such an entry the parts the compile would have built, so a
test card is made the way the compile makes one (`to_card`,
`sources_of_parts`).

    entry = with_parts(Entry(on=date(2026, 6, 12), confidence="sent", basis="",
                                   kind="text", sender="bart", body="Got an A!",
                                   event_ids=["$a", "$r"], comments=[("marge", "Proud!")]))
    to_card(entry, extraction, media={})   # two sources: Bart's line, Marge's reply

    card = diary_card_of("b48902f591", sender="marge", at=1709985600001,
                         event_ids=["$event1"], body="Bart fell off his skateboard.",
                         on=date(2024, 3, 2), kind="voice", title="Skateboard 1")
    render(card)    # the card, its words quoted under `## Sources`
"""

from __future__ import annotations

from dataclasses import replace

from stacklets.memory.bot.diary.card import Card
from stacklets.memory.bot.diary.entries import Entry, Part
from stacklets.memory.bot.diary.messages import Message
from stacklets.memory.bot.diary.sources import files_by_event, sources_of_parts


def with_parts(entry: Entry) -> Entry:
    """`entry` with the parts the compile would have built for it.

    The ids split as the compile appended them: the entry's own messages
    first, then one per reply in `comments`, last. The first message is
    the sender's, of the entry's kind, with its words, time and length; a
    message after it carries no words and no time of its own. A reply is
    a text message of who wrote it. A file is found by its event id
    (`media` in `to_card`), so a message of a kind with files names one.
    """
    ids = list(entry.event_ids)
    own = max(1, len(ids) - len(entry.comments))
    parts = []
    for n, event_id in enumerate(ids[:own]):
        first = n == 0
        parts.append(Part(Message(
            event_id, entry.sender, entry.at if first else 0, entry.kind,
            body=entry.body.strip() if first else "",
            url=None if entry.kind == "text" else f"mxc://simpson/{event_id.strip('$')}",
            duration_ms=entry.duration_ms if first else None)))
    for event_id, (who, words) in zip(ids[own:], entry.comments):
        parts.append(Part(Message(event_id, who, 0, "text", words.strip()),
                                reply=True))
    return replace(entry, parts=parts)


def diary_card_of(entry_id: str, *, sender: str, at: int, event_ids: list[str], body: str,
                  media: tuple[str, ...] | list[str] = (), **fields) -> Card:
    """The card of `sender`'s entry `entry_id`, sent at `at` (epoch ms) as
    `event_ids`, with its words `body` and its files `media`, each named
    after its event (`/media/2024/03/event1.m4a` for `$event1`); `fields`
    are the rest of the card (`on`, `kind`, `title`, `persons`, ...)."""
    fields.setdefault("confidence", "sent")
    entry = with_parts(Entry(
        on=fields.get("on"), confidence=fields["confidence"], basis="",
        kind=fields.get("kind", "text"), sender=sender, body=body, at=at,
        event_ids=list(event_ids)))
    kept = dict(files_by_event(event_ids, list(media)))
    return Card(entry_id=entry_id, sources=sources_of_parts(entry.parts, media=kept), **fields)
