"""Message groups: what one person sends in one go.

A message group is one person's messages in a room, sent in one go. famstack
forms one card from it: a diary entry in the Memories room, later a topic's
card in a topic room.

    from stack.message_groups import group_messages, is_queued

    groups = group_messages(room_messages)          # time order, any list
    for group in groups:
        group.sender, group.messages, group.replies
        group.ended_by, group.closes_at_ms, group.queued

The rule, as the family learns it: what you send in one go is one memory. A
pause of 5 minutes, or a message "--", starts the next one. A reply in a
memory's thread adds to it, at any time.

Why it is in the framework: the diary (memory) and the topic rooms (the
archivist, docs) apply the same rule, and one stacklet never imports another
stacklet's code. The module is pure and stdlib only. It reads messages that
have the fields of `Groupable` and does no I/O, so each caller keeps its own
message type.

What it does not do: dating, reading a message's words, or forming a card.
The caller's own reasons to cut a group (a message that names another day)
come in through `apart`. A group that is a flushed queue is reported
(`queued`) and not split: the caller decides what a queue becomes.

The walk, for each message in the server's time order:

1. A reply, or a message in a thread, joins the group of the message it
   answers, whenever it comes, a filed group included. It starts none and
   keeps no sender's group open. When the message it answers is not in the
   list, it is an ordinary message.
2. A close marker ("--") ends its sender's open group and belongs to none.
   The group names it as its ending message.
3. Else it joins its sender's open group when that sender's last message
   came less than the window before, and `apart` does not refuse.
4. Else it starts a new group for the sender.

Each sender has an own open group, so another person's message in between
ends nothing: Homer's photos stay one group around Marge's memo.

A group already on a card (`filed`) comes back with its messages as they
were, so regrouping with a new rule never moves a card's messages and no new
message of its own joins it. A reply in its threads still joins it, at any
time, as a reply to a message of any group does: the group comes back with
the new replies added to its `replies`. A reply to that reply joins too.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Protocol

# ── The rule's constants ──────────────────────────────────────────────

# A pause of this long ends a group. Five minutes covers a person sending
# photos and a few words while they walk about, and is short enough that a
# separate moment is a separate group.
GROUP_WINDOW_S = 300

# What phones turn "--" into. Only these characters may be in a close marker.
_DASHES = frozenset("-–—")


# ── What the caller gives and gets ────────────────────────────────────


class Groupable(Protocol):
    """A room message as the grouping reads it.

    `ts` is the server's time in epoch milliseconds. `kind` is "voice",
    "image", "video", "file" or "text". `parent` is the event id of the
    message this one answers or sits under in a thread, else None.
    `duration_ms` is the length of a recording, else None.
    """

    event_id: str
    sender: str
    ts: int
    kind: str
    duration_ms: int | None
    body: str
    parent: str | None


@dataclass(frozen=True)
class MessageGroup:
    """One person's messages sent in one go.

    `messages` are the group's own messages in order, `replies` what others
    (or the sender) added in its threads, in order. `ended_by` is the event
    id of the close marker, else of the last message. `closes_at_ms` is when
    the group stopped taking messages of its own: the marker's time, else
    the last message's time plus the window. A reply is never too late.
    `queued` says the messages are a phone's flushed queue.
    """

    sender: str
    messages: tuple[Groupable, ...]
    replies: tuple[Groupable, ...]
    queued: bool
    ended_by: str
    closes_at_ms: int

    @property
    def event_ids(self) -> tuple[str, ...]:
        """The event ids of the group's own messages, in order."""
        return tuple(m.event_id for m in self.messages)


# ── Public functions ──────────────────────────────────────────────────


def is_close_marker(text: str) -> bool:
    """Is this message the "--" that ends a group?

    After stripping spaces, the text holds only "-", "–" or "—", and at
    least two hyphens or one dash. "--" and "---" count, a single "-" does
    not, because it is a bullet or a minus. Phones change "--" into "—" or
    "–", so those count alone.
    """
    marker = text.strip()
    if not marker or not set(marker) <= _DASHES:
        return False
    return marker.count("-") >= 2 or any(c in marker for c in "–—")


def is_queued(group: MessageGroup) -> bool:
    """Do two voice messages of the group prove a flushed queue?

    A person cannot send a recording sooner after the last one than the
    recording lasts. A phone that comes back online can. Only voice
    messages with a known length count: a photo, a video or a text proves
    nothing, because it takes no time to speak.
    """
    voices = [m for m in group.messages if m.kind == "voice"]
    for before, after in zip(voices, voices[1:]):
        if after.duration_ms is not None and after.ts - before.ts < after.duration_ms:
            return True
    return False


def group_messages(
    messages: Iterable[Groupable],
    *,
    window_s: float = GROUP_WINDOW_S,
    filed: Mapping[str, MessageGroup] | None = None,
    apart: Callable[[MessageGroup, Groupable], bool] | None = None,
) -> list[MessageGroup]:
    """Group a room's messages: one group per person per go.

    Returns the groups in the time order of their first message. No messages
    give no groups. Each message is in exactly one group, a close marker in
    none.

    `window_s`: the pause, in seconds, that starts a new group.
    `filed`: event ids already on a card, each mapped to its group. That
    group comes back with its messages and replies as they were, at the
    place of its first message in the list, and no new message of its own
    joins it. A reply to one of its messages, or to a reply it has, is added
    to its `replies`: the group is then a copy with those replies at the
    end, and the same object when nothing was added. A filed group that
    none of the list's messages belongs to or answers is not returned.
    `apart(group, message)`: the caller's own reason to start a new group
    for `message`, asked only when the window alone would join it.
    """
    walk = _Walk(window_s * 1000, filed or {}, apart)
    for message in sorted(messages, key=lambda m: m.ts):
        walk.take(message)
    return walk.groups()


# ── The walk ──────────────────────────────────────────────────────────


class _Open:
    """A group while the walk builds it."""

    def __init__(self, first: Groupable):
        self.sender = first.sender
        self.messages = [first]
        self.replies: list[Groupable] = []
        self.marker: Groupable | None = None

    def add_reply(self, message: Groupable) -> None:
        self.replies.append(message)

    def freeze(self, window_ms: float) -> MessageGroup:
        last = self.messages[-1]
        if self.marker is None:
            ended_by, closes_at_ms = last.event_id, last.ts + window_ms
        else:
            ended_by, closes_at_ms = self.marker.event_id, self.marker.ts
        group = MessageGroup(
            sender=self.sender,
            messages=tuple(self.messages),
            replies=tuple(self.replies),
            queued=False,
            ended_by=ended_by,
            closes_at_ms=int(closes_at_ms),
        )
        return replace(group, queued=is_queued(group))


class _Filed:
    """A filed group while the walk runs: unchanged, but it takes replies."""

    def __init__(self, group: MessageGroup):
        self.group = group
        self.new_replies: list[Groupable] = []

    def add_reply(self, message: Groupable) -> None:
        self.new_replies.append(message)

    def freeze(self) -> MessageGroup:
        if not self.new_replies:
            return self.group
        return replace(
            self.group, replies=(*self.group.replies, *self.new_replies)
        )


class _Walk:
    """The state of one pass over the messages, in time order."""

    def __init__(self, window_ms, filed, apart):
        self._window_ms = window_ms
        self._filed = filed
        self._apart = apart
        # One slot per group in order of first appearance: a group being
        # built, or a filed group that came back as it was.
        self._slots: list[_Open | _Filed] = []
        self._open_by_sender: dict[str, _Open] = {}
        # Every event id of a group being built, its replies and its marker,
        # so a reply finds its group whatever it answers.
        self._group_of: dict[str, _Open | _Filed] = {}
        # The same for filed groups: every id a filed group already holds
        # maps to its one holder, whichever id the caller listed it under.
        self._filed_of: dict[str, _Filed] = {}
        holders: dict[int, _Filed] = {}
        for listed_id, group in filed.items():
            holder = holders.setdefault(id(group), _Filed(group))
            self._filed_of[listed_id] = holder
            for held in (*group.messages, *group.replies):
                self._filed_of[held.event_id] = holder
        self._group_of.update(self._filed_of)
        self._placed: set[int] = set()

    def take(self, message: Groupable) -> None:
        if (holder := self._filed_of.get(message.event_id)) is not None:
            self._place(holder)
        elif (parent := self._group_of.get(message.parent or "")) is not None:
            parent.add_reply(message)
            if isinstance(parent, _Filed):
                self._place(parent)
            self._group_of[message.event_id] = parent
        elif is_close_marker(message.body):
            self._close(message)
        else:
            self._join_or_start(message)

    def groups(self) -> list[MessageGroup]:
        return [
            s.freeze(self._window_ms) if isinstance(s, _Open) else s.freeze()
            for s in self._slots
        ]

    def _place(self, holder: _Filed) -> None:
        # A filed group takes the place of the first of its messages, or of
        # the first reply it gets, in the list.
        if id(holder) not in self._placed:
            self._placed.add(id(holder))
            self._slots.append(holder)

    def _close(self, marker: Groupable) -> None:
        group = self._open_by_sender.pop(marker.sender, None)
        if group is None:
            return
        group.marker = marker
        self._group_of[marker.event_id] = group

    def _join_or_start(self, message: Groupable) -> None:
        group = self._open_by_sender.get(message.sender)
        if group is not None and self._belongs(group, message):
            group.messages.append(message)
        else:
            group = _Open(message)
            self._slots.append(group)
            self._open_by_sender[message.sender] = group
        self._group_of[message.event_id] = group

    def _belongs(self, group: _Open, message: Groupable) -> bool:
        if message.ts - group.messages[-1].ts >= self._window_ms:
            return False
        return not (self._apart and self._apart(group.freeze(self._window_ms), message))
