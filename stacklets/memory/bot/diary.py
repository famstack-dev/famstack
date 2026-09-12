"""Turning a memories room into dated diary entries.

The memories room is where a family talks to its future self: voice
memos to a child, a photo with a caption, a dinner-table conversation
someone hit record on. It is also a mess. Recordings arrive days after
they were made, one memo gets split across two files, a caption shows
up ninety seconds behind its picture, and the only statement of when
something happened is a sentence spoken inside the audio.

This module is the part of the compiler that does not touch the world:
given the room's events and a reading of each transcript, it works out
what happened, when, and renders it. Every I/O concern -- paginating
Synapse, downloading audio, calling whisper, calling the model, writing
the wiki -- lives in `cli/diary.py`. Splitting it this way is what lets
the hard parts (which date wins, what is one recording and what is two)
be tested against the corpus in `tools/family-memories` without a rig.

The diary never paraphrases. The model is asked to *read* a transcript,
never to rewrite one: what lands on the page is the words that were
said. That is a promise to the reader in 2040, and it is also why the
classification step returns a small record of facts rather than prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone

# A sync burst is a phone coming back online and flushing its queue, so
# its members land seconds apart whatever their recording dates. 120s is
# the design's starting point for a real room, where live messages sit
# hours or days apart. Note that a *replayed* corpus compresses those
# day-scale gaps to seconds, so a replay needs a far smaller window --
# the value is a parameter for exactly that reason, not for tuning.
DEFAULT_BURST_WINDOW_S = 120.0


# ── What the room gives us ────────────────────────────────────────────


@dataclass(frozen=True)
class Message:
    """One room message, after Matrix mechanics and before meaning.

    `body` is the words: a transcript for voice, the caption for text,
    the filename for a photo. `ts` is server receipt time in epoch
    milliseconds, which is the *only* time Matrix records -- there is no
    compose-time field, so for anything that synced late this is days
    off. Recovering the real date is `date_for`'s problem.
    """

    event_id: str
    sender: str
    ts: int
    kind: str  # "voice" | "image" | "text"
    body: str = ""
    url: str | None = None
    duration_ms: int | None = None
    reply_to: str | None = None
    burst: str | None = None

    @property
    def sent_on(self) -> date:
        return datetime.fromtimestamp(self.ts / 1000, timezone.utc).date()


@dataclass(frozen=True)
class Reading:
    """What the classifier read out of one message.

    Facts about the text, not a rewrite of it. `spoken_date` is the date
    said aloud ("today is March sixteenth") and is the only in-band
    record of when a recording was made. The two mid-thought flags exist
    because a burst and a split recording look identical from timing
    alone: three files a second apart are three memos or one memo in
    three pieces, and only the words can tell you which.
    """

    mode: str = "monologue"  # "monologue" | "dialogue" | "note"
    spoken_date: str | None = None
    starts_mid_thought: bool = False
    ends_mid_thought: bool = False
    addressee: str | None = None


@dataclass
class Entry:
    """One thing that happened, ready to render.

    `event_ids` carries every message that fed the entry, so a rerun can
    recognise what it already compiled and a reader can trace a line on
    the page back to the recording it came from.
    """

    on: date
    confidence: str  # "spoken" | "sent" | "uncertain"
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


# ── Step 1: resolve ───────────────────────────────────────────────────
#
# Pure Matrix mechanics, no reading of meaning. Edits collapse onto the
# message they replace, reply fallbacks come off the front of bodies,
# and runs of messages that arrived together get a burst label. Nothing
# here needs a model, and getting it wrong corrupts every later step.


# Clients prepend the quoted original to a reply's body, fenced off by a
# blank line (the "rich reply fallback"). It is the same text twice; left
# in, every reply would enter the diary quoting its parent.
_FALLBACK_LINE = re.compile(r"^>.*$")


def strip_reply_fallback(body: str) -> str:
    """Drop the quoted-original block a client prepends to a reply."""
    lines = body.splitlines()
    i = 0
    while i < len(lines) and _FALLBACK_LINE.match(lines[i]):
        i += 1
    if i == 0:
        return body
    while i < len(lines) and not lines[i].strip():
        i += 1
    return "\n".join(lines[i:])


def _kind_of(msgtype: str) -> str | None:
    return {"m.audio": "voice", "m.image": "image", "m.text": "text"}.get(msgtype)


def resolve(events, *, burst_window_s: float = DEFAULT_BURST_WINDOW_S):
    """Room events to messages: edits applied, replies linked, bursts marked.

    `events` is the raw chunk from Synapse in any order; the result is
    oldest-first. Redacted events arrive with an empty content dict and
    are dropped -- the message is gone, and a diary that renders a
    tombstone is worse than one that renders nothing.
    """
    edits: dict[str, tuple[int, str]] = {}
    plain: list[Message] = []

    for ev in events:
        if ev.get("type") != "m.room.message":
            continue
        content = ev.get("content") or {}
        kind = _kind_of(content.get("msgtype", ""))
        if kind is None:
            continue

        relates = content.get("m.relates_to") or {}
        ts = ev.get("origin_server_ts") or 0

        # An edit is not a message. It is a correction to one, and only
        # the final version counts.
        if relates.get("rel_type") == "m.replace":
            target = relates.get("event_id")
            new_body = (content.get("m.new_content") or {}).get("body", "")
            if target and new_body and ts >= edits.get(target, (0, ""))[0]:
                edits[target] = (ts, new_body)
            continue

        info = content.get("info") or {}
        # For an upload, `body` is the filename and `filename` is absent;
        # a client that attaches a caption puts the caption in `body` and
        # moves the real name to `filename`. Without this the diary would
        # print "IMG_4021.png" where the caption belongs.
        body = strip_reply_fallback(content.get("body", ""))
        if kind == "image" and not content.get("filename"):
            body = ""
        plain.append(Message(
            event_id=ev.get("event_id", ""),
            sender=(ev.get("sender") or "").split(":")[0].lstrip("@"),
            ts=ts,
            kind=kind,
            body=body,
            url=content.get("url"),
            duration_ms=info.get("duration"),
            reply_to=(relates.get("m.in_reply_to") or {}).get("event_id"),
        ))

    plain.sort(key=lambda m: (m.ts, m.event_id))
    resolved = [
        replace(m, body=edits[m.event_id][1]) if m.event_id in edits else m
        for m in plain
    ]
    return mark_bursts(resolved, window_s=burst_window_s)


def mark_bursts(messages, *, window_s: float = DEFAULT_BURST_WINDOW_S):
    """Label runs of messages that reached the server together.

    A run is consecutive messages from one sender separated by less than
    `window_s`. Only runs of two or more get a label: a single message
    arriving shortly after another is just someone typing quickly, and
    calling that a burst would throw away a timestamp that is fine.

    The label means "these timestamps are arrival times, not recording
    times". It does not mean the messages belong together -- that is
    `join_fragments`, and confusing the two is the trap this corpus was
    built to set.
    """
    out = list(messages)
    run: list[int] = []
    counter = 0

    def close(run_idx):
        nonlocal counter
        if len(run_idx) < 2:
            return
        counter += 1
        label = f"burst-{counter}"
        for i in run_idx:
            out[i] = replace(out[i], burst=label)

    for i, msg in enumerate(out):
        if run:
            prev = out[run[-1]]
            same_run = (msg.sender == prev.sender
                        and (msg.ts - prev.ts) / 1000.0 < window_s)
            if same_run:
                run.append(i)
                continue
            close(run)
        run = [i]
    close(run)
    return out


# ── Step 2: join ──────────────────────────────────────────────────────


def join_fragments(messages, readings):
    """Group messages into the recordings they actually are.

    One memo split mid-sentence arrives as two files that look exactly
    like two memos sent back to back. The only evidence that separates
    them is the words: the first stops mid-thought and the second picks
    it up. So a join needs both halves to agree, and a burst label alone
    is never enough -- three same-second uploads are usually three
    independent memos.

    Returns a list of groups, each a list of messages in order.
    """
    groups: list[list[Message]] = []
    for msg in messages:
        reading = readings.get(msg.event_id, Reading())
        if groups:
            prev = groups[-1][-1]
            prev_reading = readings.get(prev.event_id, Reading())
            continues = (
                msg.kind == "voice" and prev.kind == "voice"
                and msg.sender == prev.sender
                and prev_reading.ends_mid_thought
                and reading.starts_mid_thought
            )
            if continues:
                groups[-1].append(msg)
                continue
        groups.append([msg])
    return groups


# ── Step 3: date ──────────────────────────────────────────────────────


def parse_spoken_date(value: str | None) -> date | None:
    """An ISO date the classifier heard, or None if it heard nothing."""
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def date_for(msg: Message, reading: Reading) -> tuple[date, str, str]:
    """When this happened, how sure we are, and why.

    Three sources, in order of trust:

    1. A date spoken inside the recording. It is the only one that
       describes when the thing happened rather than when a server heard
       about it, so it wins outright.
    2. The server timestamp, when the message arrived on its own. Most
       messages are sent live, so this is usually right.
    3. Nothing usable, when a message without a spoken date arrived
       inside a sync burst. The timestamp is known to be days off, so
       the entry is dated to the week it surfaced and says so. Being
       visibly unsure is the point: a confidently wrong date in a family
       diary is worse than an honest gap.
    """
    spoken = parse_spoken_date(reading.spoken_date)
    if spoken is not None:
        return spoken, "spoken", "dated from the spoken opening"
    if msg.burst:
        return msg.sent_on, "uncertain", (
            "arrived in a sync burst with no spoken date, "
            "so this is the week it surfaced, not when it happened"
        )
    return msg.sent_on, "sent", "dated from when it was sent"


# ── Step 4: compile ───────────────────────────────────────────────────


def compile_entries(messages, readings) -> list[Entry]:
    """Messages and their readings to dated diary entries.

    Two kinds of message do not earn an entry of their own. A reply
    belongs to what it replies to, and a caption that arrives behind its
    photo is that photo's caption -- rendering either separately breaks
    the pair apart and leaves a line of commentary floating with no
    subject.
    """
    groups = join_fragments(messages, readings)
    entries: list[Entry] = []
    by_event: dict[str, Entry] = {}
    pending: list[tuple[Message, list[Message]]] = []

    for group in groups:
        head = group[0]
        reading = readings.get(head.event_id, Reading())
        if head.reply_to:
            pending.append((head, group))
            continue

        on, confidence, basis = date_for(head, reading)
        entry = Entry(
            on=on,
            confidence=confidence,
            basis=basis,
            kind=head.kind,
            sender=head.sender,
            body=_joined_body(group),
            at=head.ts,
            event_ids=[m.event_id for m in group],
            addressee=reading.addressee,
            duration_ms=_total_duration(group),
            mode=reading.mode,
        )
        entries.append(entry)
        for m in group:
            by_event[m.event_id] = entry

    # Replies resolve after every parent exists, so a reply to a message
    # later in the room still finds its subject.
    for msg, group in pending:
        parent = by_event.get(msg.reply_to or "")
        if parent is None:
            reading = readings.get(msg.event_id, Reading())
            on, confidence, basis = date_for(msg, reading)
            orphan = Entry(
                on=on, confidence=confidence, basis=basis, kind=msg.kind,
                sender=msg.sender, body=_joined_body(group), at=msg.ts,
                event_ids=[m.event_id for m in group],
                addressee=reading.addressee, mode=reading.mode,
            )
            entries.append(orphan)
            by_event[msg.event_id] = orphan
            continue
        parent.comments.append((msg.sender, msg.body))
        parent.event_ids.append(msg.event_id)
        by_event[msg.event_id] = parent

    # Within a day, keep the order the room has. Sorting by event id
    # instead would scatter a day's entries into hash order, which reads
    # as randomness on the page.
    entries.sort(key=lambda e: (e.on, e.at))
    return entries


def _joined_body(group) -> str:
    """The words of a group, with a split recording read back as one."""
    parts = [m.body.strip() for m in group if m.body.strip()]
    if len(parts) < 2:
        return parts[0] if parts else ""
    # The break falls mid-sentence, so the halves join with a space
    # rather than a paragraph break -- it was one sentence when it was
    # spoken and it reads as one now.
    return " ".join(parts)


def _total_duration(group) -> int | None:
    durations = [m.duration_ms for m in group if m.duration_ms]
    return sum(durations) if durations else None


# ── Step 5: render ────────────────────────────────────────────────────
#
# The diary is a reading surface, not a report. It quotes and it links;
# it never summarises. An LLM decided what date an entry carries and
# whether two files were one recording -- it never decided what the page
# says, because the words on the page are the family's own.

DIARY_DIR = "diary"


def _duration(ms: int | None) -> str:
    if not ms:
        return ""
    total = round(ms / 1000)
    return f"{total // 60}:{total % 60:02d}"


def _kind_label(entry: Entry) -> str:
    if entry.kind == "voice":
        noun = "Conversation" if entry.mode == "dialogue" else "Voice note"
        length = _duration(entry.duration_ms)
        return f"{noun}, {length}" if length else noun
    return {"image": "Photo", "text": "Written note"}.get(entry.kind, entry.kind)


def _permalink(room_id: str, event_id: str) -> str:
    return f"https://matrix.to/#/{room_id}/{event_id}"


def _entry_block(entry: Entry, *, room_id: str) -> str:
    """One entry: who, what it was, and then their words untouched."""
    who = entry.sender.title()
    # An addressee is rendered as the message names them ("Bart", "kids"),
    # not title-cased, so a group reads as a group. A message whose
    # addressee resolves to its own sender is a misread, not a dedication.
    to = (entry.addressee or "").strip()
    if to and to.lower() != entry.sender.lower():
        heading = f"### {who} — for {to}"
    else:
        heading = f"### {who}"

    meta = [_kind_label(entry)]
    if entry.confidence != "uncertain":
        meta.append(entry.basis)
    lines = [heading, f"*{' · '.join(m for m in meta if m)}*", ""]

    if entry.confidence == "uncertain":
        lines += [
            "> [!warning] When this happened is not recoverable",
            f"> {entry.basis.capitalize()}.",
            "",
        ]

    if entry.body.strip():
        lines += [entry.body.strip(), ""]
    elif entry.kind == "image" and not entry.comments:
        lines += ["No caption came with this one.", ""]

    for who_replied, text in entry.comments:
        lines += [f"> [!quote] {who_replied.title()} replied", ]
        lines += [f"> {line}" for line in text.strip().splitlines()]
        lines.append("")

    if room_id and entry.event_ids:
        label = {"voice": "Listen in the room", "image": "See it in the room"}
        lines.append(
            f"[{label.get(entry.kind, 'Open in the room')}]"
            f"({_permalink(room_id, entry.event_ids[0])})"
        )
        lines.append("")

    return "\n".join(lines).rstrip()


def month_key(on: date) -> str:
    return on.strftime("%Y-%m")


def render_month(entries, *, room_id: str = "") -> str:
    """A month of entries, grouped by the day they happened.

    Entries whose date could not be recovered are still shown on the day
    they surfaced, under a heading that says as much. Hiding them would
    lose the memory to protect the timeline, which is the wrong trade for
    a diary.
    """
    if not entries:
        return "No entries yet."

    title = entries[0].on.strftime("%B %Y")
    count = len(entries)
    lines = [
        f"# {title}",
        "",
        f"{count} {'moment' if count == 1 else 'moments'} from the family's "
        "memories room, in the words they were recorded in. Nothing on this "
        "page has been summarised.",
        "",
    ]

    current: date | None = None
    for entry in entries:
        if entry.on != current:
            current = entry.on
            # The heading is the day. An entry that is unsure of its
            # date says so in its own block -- putting "week of" in the
            # heading would cast that doubt over every other entry
            # filed the same day.
            lines += [f"## {entry.on.strftime('%A, %-d %B')}", ""]
        lines += [_entry_block(entry, room_id=room_id), ""]

    return "\n".join(lines).rstrip() + "\n"


def render_index(entries) -> str:
    """The diary's front door: what it is, and a way into every month."""
    lines = [
        "# Family Diary",
        "",
        "Everything the family has put in the memories room: voice notes, "
        "photos, conversations someone hit record on. Entries are quoted "
        "exactly as they were said or written.",
        "",
    ]
    if not entries:
        lines += ["Nothing has been compiled yet.", ""]
        return "\n".join(lines)

    by_month: dict[str, list[Entry]] = {}
    for entry in entries:
        by_month.setdefault(month_key(entry.on), []).append(entry)

    lines += ["## Months", ""]
    for key in sorted(by_month, reverse=True):
        month = by_month[key]
        label = month[0].on.strftime("%B %Y")
        n = len(month)
        lines.append(f"- [{label}]({key}) — {n} {'entry' if n == 1 else 'entries'}")
    lines.append("")

    unsure = [e for e in entries if e.confidence == "uncertain"]
    if unsure:
        n = len(unsure)
        lines += [
            "## Dates we could not recover",
            "",
            f"{n} {'entry' if n == 1 else 'entries'} arrived in a sync burst "
            "without a spoken date. They are filed under the week they "
            "surfaced and marked on their page. Saying the date aloud at the "
            "start of a recording is what prevents this.",
            "",
        ]

    return "\n".join(lines).rstrip() + "\n"


def pages_for(entries, *, room_id: str = "") -> list[tuple[str, str, str]]:
    """Every page the diary publishes: (path, body, title).

    Paths are relative to the shared bucket, which the caller prefixes --
    the bucket is named in config (`family`, `office`, a surname) and
    this module has no business knowing which.
    """
    by_month: dict[str, list[Entry]] = {}
    for entry in entries:
        by_month.setdefault(month_key(entry.on), []).append(entry)

    out = [(f"{DIARY_DIR}/index.md", render_index(entries), "Family Diary")]
    for key, month in sorted(by_month.items()):
        out.append((
            f"{DIARY_DIR}/{key}.md",
            render_month(month, room_id=room_id),
            f"Diary: {month[0].on.strftime('%B %Y')}",
        ))
    return out
