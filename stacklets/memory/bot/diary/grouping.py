"""The message groups of a room, one diary entry each: step 2 of the diary.

    groups = group_room(messages, readings, window_s=300.0,
                        filed=filed_groups(on_file, messages),
                        card_roots=card_threads(events), now_ms=now_ms)

What a person sends in one go is one entry, until a pause of 5 minutes
or a "--" (`stack.message_groups`), with the replies in its threads. A
line sent after one's own photos is part of their entry, as its
sender's own words. A Matrix reply to one's own photo is a reply, shown
as added, because a reply joins the entry it answers and is never one
of its originals.

A message on a card keeps that card's grouping (`filed_groups`), on
every compile and on `--rebuild`, whatever rule made the card. A go that
has not ended yet (no pause of 5 minutes since its last message, no
"--") is filed on a later run: a card takes no new message of its own.

Debt (message groups): a recording cut in two by the phone is two
entries, and in a late queue the half that names no day is dated
"uncertain". No words are lost; the model no longer joins the halves,
because that guess merged separate memos. Trigger: a family reports a
memory split in the middle of a sentence. Then join the halves of a
queue by a day they share or by their order.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from stack.cards.sources import REPLY
from stack.message_groups import GROUP_WINDOW_S, MessageGroup, group_messages

from .dating import parse_spoken_date
from .entries import Reading
from .plan import cards_on_file

# How long a pause ends a message group, the diary's unit of one entry
# (`stack.message_groups`). A parameter, because a replayed corpus
# compresses the hours between live messages into seconds and needs a
# window far smaller than a real room's.
DEFAULT_GROUP_WINDOW_S = float(GROUP_WINDOW_S)


# ── Step 2: group ─────────────────────────────────────────────────────


def group_room(messages, readings, *,
               window_s: float = DEFAULT_GROUP_WINDOW_S,
               filed: "dict[str, MessageGroup] | None" = None,
               card_roots: "set[str] | frozenset[str]" = frozenset(),
               now_ms: int | None = None,
               ) -> list[MessageGroup]:
    """The room's messages as the message groups the diary makes entries of.

    What a person sends in one go is one entry: a pause of `window_s`, or
    a message "--", starts the next, and a reply joins the entry of the
    message it answers, whenever it comes (`stack.message_groups`). The
    diary adds two rules of its own:

    - A message that names a day other than the day its group names
      starts a new group, so two named days never share an entry.
    - A group that is a phone's flushed queue (`MessageGroup.queued`: a
      voice message arrived sooner after the one before than it lasts)
      becomes one group per message, because its messages were made at
      different times that the room no longer shows.

    `filed` maps the event ids of each card on file to its group
    (`filed_groups`). Such a group keeps its messages as they
    are, also when this rule would group them otherwise, and no new
    message of its own joins it; it is never split. A message in the
    thread of a card notice (`card_roots`) is a conversation with the
    archivist about the card, not a memory, and belongs to no group.

    `now_ms` is when the compile runs. A group not on file that still
    takes messages then (its `closes_at_ms` is later: no pause of
    `window_s` since its last message and no "--") is left out, with its
    replies, and comes back on a later run once it closed. A card takes
    no new message of its own, so filing a go before it ended would keep
    one go as two entries for good. `None` leaves none out.

    Returns the groups in the order of their first message. Pure: reads
    no room and calls no model; `readings` holds what the model read.
    """
    filed = filed or {}
    kept = [m for m in messages if m.thread_root not in card_roots]

    def named(message) -> date | None:
        return parse_spoken_date(readings.get(message.event_id, Reading()).spoken_date)

    def names_another_day(group: MessageGroup, message) -> bool:
        day = named(message)
        days = {d for m in group.messages if (d := named(m)) is not None}
        return day is not None and bool(days) and day not in days

    groups = group_messages(kept, window_s=window_s, filed=filed, apart=names_another_day)
    out: list[MessageGroup] = []
    for group in groups:
        on_file = group.messages[0].event_id in filed
        if not on_file and now_ms is not None and group.closes_at_ms > now_ms:
            continue
        if group.queued and not on_file:
            out += _one_group_per_message(group)
        else:
            out.append(group)
    return out


def _one_group_per_message(queue: MessageGroup) -> list[MessageGroup]:
    """A flushed queue as one group per message, each with its replies.

    Each piece stays `queued`, so its date says it arrived late when its
    words name no day. The last piece keeps the queue's ending message,
    its "--" included, so the card notice goes where the sender ended.
    """
    owner: dict[str, str] = {m.event_id: m.event_id for m in queue.messages}
    for reply in queue.replies:
        owner[reply.event_id] = owner.get(reply.parent or "", queue.messages[0].event_id)
    pieces = []
    for n, message in enumerate(queue.messages):
        last = n == len(queue.messages) - 1
        pieces.append(replace(
            queue, messages=(message,),
            replies=tuple(r for r in queue.replies if owner[r.event_id] == message.event_id),
            ended_by=queue.ended_by if last else message.event_id))
    return pieces


# The envelope types of the archivist's notices carrying an entry's card:
# the first filing, and each refiling after a correction (the archivist's
# own pattern for documents). Their thread is where a family member talks
# to the archivist about the card.
CARD_EVENTS = ("diary.filed", "diary.reclassified")


def card_threads(events) -> set[str]:
    """The roots of the threads that hold a diary card.

    Found from the room itself, like everything else the compiler knows:
    a thread is a card's thread when the archivist's card notice is in
    it. Read from the raw events, because `resolve` drops bot messages.
    """
    roots: set[str] = set()
    for ev in events:
        content = ev.get("content") or {}
        envelope = content.get("dev.famstack.event") or {}
        relates = content.get("m.relates_to") or {}
        if (isinstance(envelope, dict) and envelope.get("type") in CARD_EVENTS
                and relates.get("rel_type") == "m.thread"
                and relates.get("event_id")):
            roots.add(relates["event_id"])
    return roots


def filed_groups(existing: "dict[str, str]", messages) -> "dict[str, MessageGroup]":
    """The message group each card on file holds, by every event id on it.

    `existing` is the diary tree (path to text, cards and their sidecars),
    `messages` the room's messages (`resolve`). A card's group is
    its own messages and its replies as the card lists them, made of the
    room's messages of those ids; an id the room no longer holds is left
    out, and a card none of whose own messages the room holds has no
    group. A card dated uncertain was a message of a late queue, and its
    group says so (`queued`).

    The compile passes these to `group_room` as `filed`, so a
    message on a card keeps that card's grouping on every compile and on
    `--rebuild`, whatever rule made the card: one rule, no stored date of
    a change. Regrouping cards on file is a separate request.
    """
    by_id = {m.event_id: m for m in messages}
    out: dict[str, MessageGroup] = {}
    for _, card in cards_on_file(existing).values():
        own = [by_id[i] for i in list(dict.fromkeys([s.id for s in card.originals])) if i in by_id]
        if not own:
            continue
        replies = [by_id[i] for i in list(dict.fromkeys([s.id for s in card.sources if s.role == REPLY]))
                   if i in by_id]
        group = MessageGroup(sender=own[0].sender, messages=tuple(own), replies=tuple(replies),
                             queued=card.confidence == "uncertain",
                             ended_by=own[-1].event_id, closes_at_ms=own[-1].ts)
        for message in (*own, *replies):
            out.setdefault(message.event_id, group)
    return out
