"""Diary entries: one per message group, dated, ready to render (step 4).

    entries = compile_entries(groups, readings)
    verify_moments(entry.body, ["Bart played the saxophone."])   # the quotes that hold

A `Reading` is what the model read out of one message; an `Entry` is one
thing that happened, its `Part`s the messages it was compiled from.
`compile_entries` dates each group with `dating.date_for` and joins its
words. `verify_moments` keeps only the quotes a model claimed that the
body holds word for word, because the diary quotes and never invents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import cast

from stack.message_groups import MessageGroup

from .dating import date_for
from .messages import Message


@dataclass(frozen=True)
class Reading:
    """What the model read out of one message.

    Facts about the text, not a rewrite of it. `spoken_date` is the day
    the words name, typed or said aloud ("today is March sixteenth"), and
    is the only in-band record of when something happened.

    Which messages belong together is not here: what a person sends in
    one go is one entry (`group_room`), whatever the words say.
    """

    mode: str = "monologue"  # "monologue" | "dialogue" | "note"
    spoken_date: str | None = None
    addressee: str | None = None


@dataclass(frozen=True)
class Part:
    """One message of an entry, in the order the room has it: the entry's
    own (`reply` false) or a reply to it, a remark on it included.

    The card keeps one source per part (ADR-018 §2), so a caption stays
    with its photo and a reply keeps its day.

        Part(Message("$p1", "homer", 1762794004000, "image", url="mxc://s/p1"))
        Part(Message("$r1", "marge", 1762843512000, "text", "So cute!"), reply=True)
    """

    message: Message
    reply: bool = False


@dataclass
class Entry:
    """One thing that happened, ready to render.

    `event_ids` carries every message that fed the entry, so a rerun can
    recognise what it already compiled and a reader can trace a line on
    the page back to the recording it came from.
    """

    on: date
    confidence: str  # the basis of its day (`dating`)
    basis: str
    kind: str
    sender: str
    body: str
    at: int = 0  # arrival time of the first message, for same-day ordering
    event_ids: list[str] = field(default_factory=list)
    addressee: str | None = None
    duration_ms: int | None = None
    mode: str = "monologue"
    comments: list[tuple[str, str]] = field(default_factory=list)
    # Distilled view for long recordings: one narrative sentence and
    # verified word-for-word quotes, both from the entry's diary card
    # (`card.to_entry`). Empty for short entries; the renderer
    # then shows the body in full.
    gist: str = ""
    moments: list[str] = field(default_factory=list)
    # The card's title, when the entry comes from a card. Heads the entry
    # on the page.
    title: str = ""
    # The card's entry id, when the entry comes from a card. Anchors the
    # entry on the page, so `/go/diary/<entry_id>` lands on it.
    entry_id: str = ""
    # The card's own id and the version of it the entry was read from, so
    # a page names the cards it shows (`Page.entries`).
    card_id: str = ""
    version: str = ""
    # The messages the entry was compiled from, in the order sent, its
    # replies included. Empty for an entry read back from a card, whose
    # sources hold the same.
    parts: list[Part] = field(default_factory=list)
    # The message that ended the entry's message group: its "--", else its
    # last message. The card notice goes in its thread. Empty for an entry
    # read back from a card.
    ended_by: str = ""


# ── Step 4: compile ───────────────────────────────────────────────────


def compile_entries(groups, readings) -> list[Entry]:
    """Message groups and their readings to dated diary entries.

    One entry per group (`group_room`). Each of its messages is a part of
    the entry, in the order sent: a line its sender wrote under their own
    photos is the entry's own, not a reply. A reply in its threads is a
    part marked as a reply, with its person and its day. Whom the entry is
    for is the first addressee its messages name; how its words were
    spoken is its first message's reading.

    Entries come back ordered by day, and within a day in the order the
    room has them.
    """
    entries = [_entry(group, readings) for group in groups]
    # Within a day, keep the order the room has. Sorting by event id
    # instead would scatter a day's entries into hash order, which reads
    # as randomness on the page.
    entries.sort(key=lambda e: (e.on, e.at))
    return entries


def _own(group: MessageGroup) -> tuple[Message, ...]:
    """A group's own messages, as the diary's messages: `group_room` groups
    only messages `resolve` made."""
    return cast("tuple[Message, ...]", group.messages)


def _replies(group: MessageGroup) -> tuple[Message, ...]:
    """A group's replies, as the diary's messages (see `_own`)."""
    return cast("tuple[Message, ...]", group.replies)


def _entry(group: MessageGroup, readings) -> Entry:
    """The entry of one message group."""
    own, replies = _own(group), _replies(group)
    head = own[0]
    on, confidence, basis = date_for(group, readings)
    read = [readings.get(m.event_id, Reading()) for m in own]
    return Entry(
        on=on,
        confidence=confidence,
        basis=basis,
        kind=head.kind,
        sender=head.sender,
        body=_joined_body(own),
        at=head.ts,
        event_ids=[m.event_id for m in (*own, *replies)],
        addressee=next((r.addressee for r in read if r.addressee), None),
        duration_ms=_total_duration(own),
        mode=read[0].mode,
        comments=[(r.sender, r.body) for r in replies],
        parts=[Part(m) for m in own] + [Part(r, reply=True) for r in replies],
        ended_by=group.ended_by,
    )


# A distilled entry shows at most this many quotes.
_MAX_MOMENTS = 3

# Entries below this length render in full; a gist would only repeat
# them. At or above it, the renderer prefers the distilled view.
DISTILL_MIN_WORDS = 120

_SENTENCE_END = re.compile(r"(?<=[.!?\u2026])\s+")


def _normalized(text: str) -> str:
    """Text reduced to lowercase letters and digits, for comparison."""
    return re.sub(r"[^a-z0-9\u00c0-\u024f]+", "", text.lower())


def verify_moments(body: str, claimed) -> list[str]:
    """Return the claimed quotes that the body really contains.

    The model copies passages; this function checks them. A claimed
    quote is accepted when it equals one sentence of the body, or a run
    of consecutive sentences, compared without case and punctuation.
    The returned text is the body's own text, not the model's copy, so
    a quote on the page is an exact excerpt. Claims that match nothing
    are dropped.
    """
    sentences = [x.strip() for x in _SENTENCE_END.split(body) if x.strip()]
    norms = [_normalized(x) for x in sentences]
    kept: list[str] = []
    for claim in claimed or ():
        want = _normalized(str(claim))
        if not want:
            continue
        found = None
        for i in range(len(norms)):
            joined = ""
            for j in range(i, len(norms)):
                joined += norms[j]
                if joined == want:
                    found = " ".join(sentences[i:j + 1])
                    break
                if len(joined) > len(want):
                    break
            if found:
                break
        if found and found not in kept:
            kept.append(found)
        if len(kept) >= _MAX_MOMENTS:
            break
    return kept


def _joined_body(messages) -> str:
    """The words of an entry's own messages, as one text.

    Joined with a space, as the card reads its words back
    (`card._joined`): the card's sources keep each message apart,
    and this text is what the model reads and the card is compared by.
    """
    parts = [m.body.strip() for m in messages if m.body.strip()]
    return " ".join(parts)


def _total_duration(group) -> int | None:
    durations = [m.duration_ms for m in group if m.duration_ms]
    return sum(durations) if durations else None
