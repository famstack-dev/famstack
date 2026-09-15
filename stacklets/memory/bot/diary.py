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

The diary quotes, it does not invent. Words shown as someone's own —
a quote, a transcript — come from the recording unchanged. Narrative
text (summaries, chronicle paragraphs) is permitted and renders as
narrative, never as quotation. Every entry links to its source events,
and the audio stays the archival original. The classification step
returns a small record of facts rather than prose for the same reason:
generated text must never blend into quoted text.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone, tzinfo

# A sync burst is a phone coming back online and flushing its queue, so
# its members land seconds apart whatever their recording dates. 120s is
# the design's starting point for a real room, where live messages sit
# hours or days apart. Note that a *replayed* corpus compresses those
# day-scale gaps to seconds, so a replay needs a far smaller window --
# the value is a parameter for exactly that reason, not for tuning.
DEFAULT_BURST_WINDOW_S = 120.0


# ── Language ──────────────────────────────────────────────────────────
#
# Every string a family member reads on a page comes from this table.
# The compiler selects the household language once, at startup, via
# configure_language(). English is the default and the test baseline.

_STRINGS = {
    "en": {
        "and": "and",
        "months": ["January", "February", "March", "April", "May",
                   "June", "July", "August", "September", "October",
                   "November", "December"],
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday"],
        "day_heading": "{day}, {dom} {month}",
        "vocab_people": "The people in this family are {names}.",
        "vocab_topics": "They often talk about {topics}.",
        "basis_spoken": "dated from the spoken opening",
        "basis_sent": "dated from when it was sent",
        "basis_burst": ("arrived in a sync burst with no spoken date, "
                        "so this is the week it surfaced, not when it "
                        "happened"),
        "kind_image": "Photo", "kind_video": "Video",
        "kind_file": "File", "kind_text": "Written note",
        "kind_attachment": "Attachment",
        "kind_voice": "Voice note", "kind_dialogue": "Conversation",
        "for": "for",
        "when_unknown": "When this happened is not recoverable",
        "untranscribable": "This recording could not be transcribed.",
        "nothing_written": "Nothing was written alongside this one.",
        "full_transcript": "Full transcript",
        "replied": "{who} replied",
        "link_voice": "Listen in the room",
        "link_image": "See it in the room",
        "link_video": "Watch it in the room",
        "link_other": "Open in the room",
        "no_entries": "No entries yet.",
        "entry_one": "entry", "entry_many": "entries",
        "month_one": "month", "month_many": "months",
        "year_opening": "{count} this year",
        "recorded_by": "recorded by",
        "months_h": "Months", "years_h": "Years",
        "diary_title": "Family Diary",
        "home_teaser": "Recordings and notes, read back month by month.",
        "index_intro": (
            "Your memories, kept in a chronicle to read back. Voice "
            "notes, photos, conversations you recorded. Every entry "
            "leads back to the original recording, there to be "
            "listened to, today or in twenty years."),
        "getting_started": """## Nothing recorded yet

This diary is written from **Memories**, the room in your family chat.
Whatever you send there becomes an entry here: a voice message, a photo,
a video, or a few lines of writing.

- Record a voice message. It is written out here in full, and the
  recording stays one tap away.
- Open with the date ("Today is the third of March") and the entry is
  filed on that day. Without one, the day you sent it counts.
- Reply to a message to add to that memory later.

There is no wrong way to use it. Press record.""",
        "across": "across",
    },
    "de": {
        "and": "und",
        "months": ["Januar", "Februar", "M\u00e4rz", "April", "Mai",
                   "Juni", "Juli", "August", "September", "Oktober",
                   "November", "Dezember"],
        "days": ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
                 "Freitag", "Samstag", "Sonntag"],
        "day_heading": "{day}, {dom}. {month}",
        "vocab_people": "Die Personen in dieser Familie sind {names}.",
        "vocab_topics": "Sie sprechen oft \u00fcber {topics}.",
        "basis_spoken": "datiert nach dem gesprochenen Datum",
        "basis_sent": "datiert nach dem Sendezeitpunkt",
        "basis_burst": ("kam in einem Sync-Schub ohne gesprochenes "
                        "Datum an; eingeordnet in der Woche des "
                        "Auftauchens, nicht des Geschehens"),
        "kind_image": "Foto", "kind_video": "Video",
        "kind_file": "Datei", "kind_text": "Notiz",
        "kind_attachment": "Anhang",
        "kind_voice": "Sprachnotiz", "kind_dialogue": "Gespr\u00e4ch",
        "for": "f\u00fcr",
        "when_unknown": ("Wann dies geschah, l\u00e4sst sich nicht "
                         "mehr feststellen"),
        "untranscribable": ("Diese Aufnahme konnte nicht "
                            "transkribiert werden."),
        "nothing_written": "Hierzu wurde nichts geschrieben.",
        "full_transcript": "Vollst\u00e4ndiges Transkript",
        "replied": "{who} antwortete",
        "link_voice": "Im Chat anh\u00f6ren",
        "link_image": "Im Chat ansehen",
        "link_video": "Im Chat ansehen",
        "link_other": "Im Chat \u00f6ffnen",
        "no_entries": "Noch keine Eintr\u00e4ge.",
        "entry_one": "Eintrag", "entry_many": "Eintr\u00e4ge",
        "month_one": "Monat", "month_many": "Monaten",
        "year_opening": "{count} in diesem Jahr",
        "recorded_by": "aufgenommen von",
        "months_h": "Monate", "years_h": "Jahre",
        "diary_title": "Familientagebuch",
        "home_teaser": ("Aufnahmen und Notizen, Monat f\u00fcr Monat "
                        "zum Nachlesen."),
        "index_intro": (
            "Eure Erinnerungen, festgehalten in einer Chronik zum "
            "Nachlesen. Sprachnotizen, Fotos, Gespr\u00e4che, die ihr "
            "aufgenommen habt. Jeder Eintrag f\u00fchrt zur\u00fcck "
            "zur Originalaufnahme, zum Nachh\u00f6ren, heute "
            "oder in zwanzig Jahren."),
        "getting_started": """## Noch nichts aufgenommen

Dieses Tagebuch entsteht aus **Memories**, dem Raum in eurem Familienchat.
Alles, was ihr dort sendet, wird hier zu einem Eintrag: eine Sprachnachricht,
ein Foto, ein Video oder ein paar Zeilen Text.

- Nehmt eine Sprachnachricht auf. Sie wird hier vollst\u00e4ndig
  ausgeschrieben, und die Aufnahme bleibt einen Fingertipp entfernt.
- Beginnt mit dem Datum ("Heute ist der dritte M\u00e4rz"), dann wird der
  Eintrag auf diesen Tag datiert. Ohne Datum z\u00e4hlt der Tag, an dem
  ihr gesendet habt.
- Antwortet auf eine Nachricht, um sp\u00e4ter etwas zu erg\u00e4nzen.

Es gibt kein falsches Vorgehen. Dr\u00fcckt auf Aufnahme.""",
        "across": "in",
    },
}

_L = _STRINGS["en"]


def configure_language(code: str) -> None:
    """Select the render language. Unknown codes keep English."""
    global _L
    _L = _STRINGS.get((code or "").strip().lower()[:2], _STRINGS["en"])


def _month_name(on: date) -> str:
    return _L["months"][on.month - 1]


def _month_year(on: date) -> str:
    return f"{_month_name(on)} {on.year}"


def _day_heading(on: date) -> str:
    return _L["day_heading"].format(
        day=_L["days"][on.weekday()], dom=on.day, month=_month_name(on))


def _counted(n: int, one: str, many: str) -> str:
    return f"{n} {_L[one] if n == 1 else _L[many]}"


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
    kind: str  # "voice" | "image" | "video" | "file" | "text"
    body: str = ""
    url: str | None = None
    duration_ms: int | None = None
    reply_to: str | None = None
    burst: str | None = None
    zone: tzinfo = timezone.utc

    @property
    def sent_on(self) -> date:
        """The calendar day the family would say this happened on.

        Read in the household's timezone, not UTC. A memo recorded at
        half past midnight in Berlin is a UTC message from the previous
        day, and filing it there puts it under the wrong heading in a
        diary whose whole job is saying when things happened.
        """
        return datetime.fromtimestamp(self.ts / 1000, self.zone).date()


@dataclass(frozen=True)
class Reading:
    """What the model read out of one message.

    Facts about the text, not a rewrite of it. `spoken_date` is the date
    said aloud ("today is March sixteenth") and is the only in-band
    record of when a recording was made.

    Relationships between messages are not here. They live in the
    `continues` and `refers_to` maps, because reading one message can
    never establish them: whether an upload finishes the one before it,
    or whether a line of text is about the photo above it, is only
    visible to something looking at both.
    """

    mode: str = "monologue"  # "monologue" | "dialogue" | "note"
    spoken_date: str | None = None
    addressee: str | None = None
    # Distillation, for long recordings. `gist` is one narrative
    # sentence about the message. `moments` are passages the model
    # copied from the text; verify_moments() checks each one against
    # the body before it can render as a quote.
    gist: str | None = None
    moments: tuple = ()


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
    # Distilled view for long recordings: one narrative sentence and
    # verified word-for-word quotes. Empty for short entries; the
    # renderer then shows the body in full.
    gist: str = ""
    moments: list[str] = field(default_factory=list)


# ── What this household says ──────────────────────────────────────────


def spoken_vocabulary(people, topics=()) -> str:
    """A hint for whisper about the words this family uses.

    Whisper decodes against it, so names it would otherwise hear as
    ordinary words come back right. This is the only place a name can be
    fixed: the polish pass is forbidden from changing words, and it is
    right to be -- the memories room holds what people said to their
    children, and a model quietly editing that is not a transcript any
    more. So the input is corrected instead of the output.

    Phrased as a sentence rather than a bare list because that is what
    the parameter is for: whisper treats it as preceding speech, and a
    list of nouns biases the decoder toward answering in lists.
    """
    names = [n.strip() for n in people if n and n.strip()]
    subjects = [t.strip() for t in topics if t and t.strip()]
    parts = []
    if names:
        parts.append(_L["vocab_people"].format(
            names=_and_list(_unique(names))))
    if subjects:
        parts.append(_L["vocab_topics"].format(
            topics=_and_list(_unique(subjects))))
    return " ".join(parts)


def _unique(values):
    seen = []
    for v in values:
        if v not in seen:
            seen.append(v)
    return seen


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
    """Drop the quoted-original block a client prepends to a reply.

    Only ever called for a message that really is a reply. A leading
    blockquote is otherwise just a leading blockquote, and stripping it
    unconditionally would silently eat the opening of any entry that
    starts by quoting something.
    """
    lines = body.splitlines()
    i = 0
    while i < len(lines) and _FALLBACK_LINE.match(lines[i]):
        i += 1
    if i == 0:
        return body
    while i < len(lines) and not lines[i].strip():
        i += 1
    return "\n".join(lines[i:])


# Uploads whose `body` is a filename rather than words. A caption, when
# a client sends one, displaces it (MSC2530) and the real name moves to
# `filename`.
_UPLOADS = ("image", "video", "file")


def _kind_of(msgtype: str) -> str | None:
    """The kind of entry a message makes, or None to ignore it.

    Video and arbitrary files count. A family posts a clip of a first
    step to the memories room as readily as a photo, and a compiler that
    recognised only the three types its test corpus happened to contain
    would drop it without saying so.
    """
    return {
        "m.audio": "voice", "m.image": "image", "m.video": "video",
        "m.file": "file", "m.text": "text",
    }.get(msgtype)


# Bot accounts are named by convention: a localpart ending in `-bot`.
# The framework owns that definition (`MicroBot.is_bot_user`), which we
# cannot import here without pulling a Matrix client into a module that
# is pure on purpose. What a bot posts in the room is instruction, not
# memory, and a diary that opens with the welcome message opens with
# someone else's words.
_BOT_SUFFIX = "-bot"


def resolve(events, *, burst_window_s: float = DEFAULT_BURST_WINDOW_S,
            zone: tzinfo = timezone.utc):
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
        sender = (ev.get("sender") or "").split(":")[0].lstrip("@")
        if sender.endswith(_BOT_SUFFIX):
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
        in_reply_to = (relates.get("m.in_reply_to") or {}).get("event_id")

        body = content.get("body", "")
        if in_reply_to:
            body = strip_reply_fallback(body)
        if kind in _UPLOADS:
            # `body` is the filename until a caption displaces it, at
            # which point the name moves to `filename`. Clients that set
            # `filename` to the same string are still sending a bare
            # upload, so compare rather than test for presence -- else
            # the diary prints "IMG_4021.png" where a caption belongs.
            filename = content.get("filename")
            body = body if (filename and filename != body) else ""

        plain.append(Message(
            event_id=ev.get("event_id", ""),
            sender=sender,
            ts=ts,
            kind=kind,
            body=body,
            url=content.get("url"),
            duration_ms=info.get("duration"),
            reply_to=in_reply_to,
            zone=zone,
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

    The label means only "these arrived together". Whether that makes
    them a *sync* burst -- a queue being flushed, whose timestamps are
    days off -- is `confirm_bursts`'s question, and it needs evidence
    this function does not have. Nor does arriving together mean the
    messages belong together: that is `join_fragments`, and confusing
    the two is the trap this corpus was built to set.
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


# How far a spoken date must sit from its own timestamp before that
# timestamp is provably not the recording date. A day of slack absorbs
# the honest cases: a memo recorded before midnight that reaches the
# server after it, or a household clock a few hours off UTC.
CONTRADICTION_DAYS = 1


def confirm_bursts(messages, readings):
    """Keep the burst label only where the timestamps are provably lying.

    Arriving together is not evidence of anything by itself. A family
    recording three memos at the dinner table sends them a minute apart,
    and those timestamps are perfectly good; marking that run a sync
    burst would file three entries as undateable when nothing was wrong
    with any of them. That is the failure worth avoiding, because a
    diary that cries uncertainty over ordinary evenings teaches the
    family to ignore the warning on the one entry that earned it.

    So a run must contradict itself: some message in it says aloud that
    it was made on a day its own timestamp disagrees with. That is proof
    the queue was flushed rather than lived, and it makes the rest of
    the run suspect too.

    A synced burst in which nobody spoke a date is therefore dated as
    though it were live. Wrong, but undetectably so -- there is no
    signal in the room to find, and inventing suspicion from timing
    alone costs more than it recovers.
    """
    lying = set()
    for msg in messages:
        if not msg.burst:
            continue
        spoken = parse_spoken_date(
            readings.get(msg.event_id, Reading()).spoken_date)
        if spoken is None:
            continue
        if abs((msg.sent_on - spoken).days) > CONTRADICTION_DAYS:
            lying.add(msg.burst)

    return [m if m.burst in lying else replace(m, burst=None)
            for m in messages]


# ── Step 2: join ──────────────────────────────────────────────────────


def join_fragments(messages, continues):
    """Group messages into the recordings they actually are.

    One memo split mid-sentence arrives as two files that look exactly
    like two memos sent back to back, and timing cannot tell them apart:
    three uploads in one second are usually three separate thoughts.
    Only the words decide, and only to something reading both halves at
    once -- which is why `continues` is handed in, mapping a message to
    the one it finishes.

    The link is still checked against the room: a join must be with the
    message immediately before, from the same person, of the same kind.
    A recording cut in two arrives as two adjacent uploads, so a link
    reaching further than that is a misreading, and merging on it would
    fuse two unrelated memories into one entry.

    Returns a list of groups, each a list of messages in order.
    """
    groups: list[list[Message]] = []
    holding: dict[str, list[Message]] = {}

    for i, msg in enumerate(messages):
        target = continues.get(msg.event_id)
        previous = messages[i - 1] if i else None
        adjacent = (
            previous is not None
            and target == previous.event_id
            and msg.sender == previous.sender
            and msg.kind == previous.kind
        )
        group = holding.get(target) if adjacent else None
        if group is None:
            group = [msg]
            groups.append(group)
        else:
            group.append(msg)
        holding[msg.event_id] = group

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
        return spoken, "spoken", _L["basis_spoken"]
    if msg.burst:
        return msg.sent_on, "uncertain", _L["basis_burst"]
    return msg.sent_on, "sent", _L["basis_sent"]


# ── Step 4: compile ───────────────────────────────────────────────────


# How long a remark may trail the thing it is about. A caption is posted
# while its photo is still on screen; a follow-up months later is its own
# memory, however clearly it answers an older one. Time rather than
# position, so a busy evening and a quiet week are judged the same way.
REMARK_WINDOW_S = 3600.0


def _remarks_only_on_what_is_still_in_view(messages, refers_to):
    """Drop `refers_to` links that reach back beyond living memory.

    The model is asked which messages are remarks about an earlier one
    rather than memories of their own, and it is right about captions.
    It also, given a recording that ends "the cast comes off in four
    weeks" and a note four weeks later saying it did, reads the second
    as a remark on the first. They are related; that does not make the
    later one a footnote. Filing it as one costs it its own date, its
    own place in the month, and credits it as a reply nobody made.

    So the room checks the reading, as it does for joined recordings: a
    remark belongs to something still in view when it was written.
    """
    at = {m.event_id: m.ts for m in messages}
    kept = {}
    for child, parent in refers_to.items():
        if child not in at or parent not in at:
            continue
        gap = (at[child] - at[parent]) / 1000.0
        if 0 <= gap <= REMARK_WINDOW_S:
            kept[child] = parent
    return kept


def compile_entries(messages, readings, *,
                    continues: "dict[str, str] | None" = None,
                    refers_to: "dict[str, str] | None" = None) -> list[Entry]:
    """Messages and their readings to dated diary entries.

    Some messages do not earn an entry of their own. A reply belongs to
    what it replies to; so does a caption trailing its photo, and so
    does a line like "the picture above is from the barbecue" -- which
    carries no Matrix relation at all and is only recognisable to
    something that read the two together. `refers_to` carries those.
    Rendering any of them separately breaks the pair apart and leaves a
    remark floating with no subject.
    """
    continues = continues or {}
    about = _remarks_only_on_what_is_still_in_view(messages, refers_to or {})

    # Join first (a split recording is two adjacent uploads), then
    # decide which runs were really a queue being flushed. Dating reads
    # the confirmed view.
    groups = join_fragments(messages, continues)
    confirmed = {m.event_id: m for m in confirm_bursts(messages, readings)}
    entries: list[Entry] = []
    by_event: dict[str, Entry] = {}
    pending: list[tuple[Message, list[Message]]] = []

    for group in groups:
        head = confirmed.get(group[0].event_id, group[0])
        reading = readings.get(head.event_id, Reading())
        parent = head.reply_to or about.get(head.event_id)
        if parent:
            pending.append((replace(head, reply_to=parent), group))
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
            gist=(reading.gist or "").strip(),
            moments=verify_moments(_joined_body(group), reading.moments),
        )
        entries.append(entry)
        for m in group:
            by_event[m.event_id] = entry

    # Replies resolve after every parent exists, so a reply to a message
    # later in the room still finds its subject.
    for msg, group in pending:
        parent = by_event.get(msg.reply_to or "")
        if parent is None:
            msg = confirmed.get(msg.event_id, msg)
            reading = readings.get(msg.event_id, Reading())
            on, confidence, basis = date_for(msg, reading)
            orphan = Entry(
                on=on, confidence=confidence, basis=basis, kind=msg.kind,
                sender=msg.sender, body=_joined_body(group), at=msg.ts,
                event_ids=[m.event_id for m in group],
                addressee=reading.addressee, mode=reading.mode,
                gist=(reading.gist or "").strip(),
                moments=verify_moments(_joined_body(group), reading.moments),
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


# What each kind of entry is called on the page. Every kind the
# compiler accepts needs a name here: falling through to the internal
# word prints "video" in a line of otherwise written English.
def _kind_label(entry: Entry) -> str:
    if entry.kind == "voice":
        noun = (_L["kind_dialogue"] if entry.mode == "dialogue"
                else _L["kind_voice"])
    else:
        noun = _L.get(f"kind_{entry.kind}", _L["kind_attachment"])
    length = _duration(entry.duration_ms)
    return f"{noun}, {length}" if length else noun


def _distills(entry: Entry) -> bool:
    """Whether the renderer shows the distilled view for this entry.

    Requires a gist and a long body. Without a gist the full text is
    the only faithful rendering. Short entries are already the right
    amount of detail. A message addressed to one person never
    distills, whatever its length: it is a personal message, and the
    diary posts it whole, framed by the addressee in its heading.
    """
    return (bool(entry.gist)
            and not (entry.addressee or "").strip()
            and len(entry.body.split()) >= DISTILL_MIN_WORDS)


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
        heading = f"### {who} — {_L['for']} {to}"
    else:
        heading = f"### {who}"

    # How the date was derived is our concern, not the reader's, and it
    # would repeat under every entry on every page. Where it matters,
    # because we could not derive one, the callout below says so.
    lines = [heading, f"*{_kind_label(entry)}*", ""]

    if entry.confidence == "uncertain":
        lines += [
            f"> [!warning] {_L['when_unknown']}",
            f"> {entry.basis.capitalize()}.",
            "",
        ]

    if entry.body.strip() and _distills(entry):
        # The distilled view for long recordings: one narrative line,
        # verified quotes, and the full transcript in a folded block.
        # verify_moments() guarantees each quote is an exact excerpt.
        lines += [entry.gist, ""]
        for moment in entry.moments:
            lines += [f"> [!quote] {moment}", ""]
        lines += [f"> [!note]- {_L['full_transcript']}"]
        lines += [f"> {line}" if line.strip() else ">"
                  for line in entry.body.strip().splitlines()]
        lines += [""]
    elif entry.body.strip():
        lines += [entry.body.strip(), ""]
    elif entry.kind == "voice" and not entry.comments:
        # A gated recording: the transcript was unusable and the words
        # stay off the page. The sentence tells the reader this is
        # deliberate. The audio link below stays the way to hear it.
        lines += [_L["untranscribable"], ""]
    elif entry.kind in _UPLOADS and not entry.comments:
        lines += [_L["nothing_written"], ""]

    for who_replied, text in entry.comments:
        lines += ['> [!quote] ' + _L['replied'].format(who=who_replied.title())]
        lines += [f"> {line}" for line in text.strip().splitlines()]
        lines.append("")

    if room_id and entry.event_ids:
        label = {"voice": _L["link_voice"], "image": _L["link_image"],
                 "video": _L["link_video"]}
        lines.append(
            f"[{label.get(entry.kind, _L['link_other'])}]"
            f"({_permalink(room_id, entry.event_ids[0])})"
        )
        lines.append("")

    return "\n".join(lines).rstrip()


def year_key(on: date) -> str:
    return on.strftime("%Y")


def month_key(on: date) -> str:
    """A month's own slug, which is its number.

    The year is already the folder, so the file is `03.md` rather than
    `2026-03.md`: it reads as `/diary/2026/03`, and numbering sorts the
    explorer chronologically where month names would sort April before
    March.
    """
    return on.strftime("%m")


def month_digest(entries) -> str:
    """A fingerprint of everything a month's summary was written from.

    The summary is cached against this, so it is recomputed exactly when
    the month's content moves and not otherwise. It covers what the
    summariser is shown -- who, when, and the words, including remarks
    attached later -- so a memo surfacing months after the fact rewrites
    the page it lands on, while an untouched month keeps its paragraph
    word for word rather than being quietly reworded every night.
    """
    parts = []
    for entry in sorted(entries, key=lambda e: (e.on, e.at)):
        parts.append("\x1f".join([
            ",".join(entry.event_ids), entry.on.isoformat(),
            entry.confidence, entry.sender, entry.body,
            "|".join(f"{who}:{text}" for who, text in entry.comments),
        ]))
    return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()


def _by_year(entries) -> "dict[str, list[Entry]]":
    out: dict[str, list[Entry]] = {}
    for entry in entries:
        out.setdefault(year_key(entry.on), []).append(entry)
    return out


def _by_month(entries) -> "dict[str, list[Entry]]":
    out: dict[str, list[Entry]] = {}
    for entry in entries:
        out.setdefault(month_key(entry.on), []).append(entry)
    return out


def _recorded_by(entries) -> list[str]:
    """Who captured a year's entries, in order of first appearance.

    Senders only. A sender is a Matrix account and is therefore a fact;
    an addressee is the model's reading of who was spoken to, and a
    landing page is the wrong place for a guess -- it reads as a roster
    of the household. Addressees stay on the entries themselves, where
    a misread is visible next to the words that caused it.
    """
    seen: list[str] = []
    for entry in entries:
        name = (entry.sender or "").strip()
        pretty = name.title() if name.islower() else name
        if pretty and pretty not in seen:
            seen.append(pretty)
    return seen


def render_month(entries, *, room_id: str = "", summary: str = "") -> str:
    """A month of entries, grouped by the day they happened.

    `summary` is an optional paragraph recalling the month. It opens
    the page as narrative. Material quoted below it is word-for-word
    from the recordings. The full promise is stated once, on the
    diary's front page, not on every month.

    Entries whose date could not be recovered are still shown on the day
    they surfaced, under a heading that says as much. Hiding them would
    lose the memory to protect the timeline, which is the wrong trade for
    a diary.
    """
    if not entries:
        return _L["no_entries"]

    lines = [f"# {_month_year(entries[0].on)}", ""]
    if summary.strip():
        lines += [summary.strip(), ""]

    current: date | None = None
    for entry in entries:
        if entry.on != current:
            current = entry.on
            # The heading is the day. An entry that is unsure of its
            # date says so in its own block -- putting "week of" in the
            # heading would cast that doubt over every other entry
            # filed the same day.
            lines += [f"## {_day_heading(entry.on)}", ""]
        lines += [_entry_block(entry, room_id=room_id), ""]

    return "\n".join(lines).rstrip() + "\n"


def render_year(entries) -> str:
    """A year's landing page: its months, and who is in them.

    Deterministic. Counting entries and naming the people who appear is
    reading, not summarising, so nothing here needs a model and nothing
    here can drift between runs.
    """
    year = entries[0].on.strftime("%Y")
    lines = [f"# {year}", ""]

    n = len(entries)
    people = _recorded_by(entries)
    opening = _L["year_opening"].format(
        count=_counted(n, "entry_one", "entry_many"))
    if people:
        opening += f", {_L['recorded_by']} {_and_list(people)}"
    lines += [opening + ".", "", f"## {_L['months_h']}", ""]

    for key, month in sorted(_by_month(entries).items()):
        label = _month_name(month[0].on)
        count = len(month)
        lines.append(
            f"- [{label}]({key}): "
            f"{_counted(count, 'entry_one', 'entry_many')}")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_index(entries) -> str:
    """The diary's front door: what it is, and a way into every year."""
    lines = [
        f"# {_L['diary_title']}",
        "",
        _L["index_intro"],
        "",
    ]
    if not entries:
        # An empty diary is the one moment a family needs to be told
        # how to fill one. This page is where the wiki's diary link
        # lands, so it has to answer "what now" rather than report a
        # count of zero.
        lines += [_L["getting_started"], ""]
        return "\n".join(lines).rstrip() + "\n"

    lines += [f"## {_L['years_h']}", ""]
    for key, year in sorted(_by_year(entries).items(), reverse=True):
        count = len(year)
        months = len(_by_month(year))
        lines.append(
            f"- [{key}]({key}/about): "
            f"{_counted(count, 'entry_one', 'entry_many')} "
            f"{_L['across']} "
            f"{_counted(months, 'month_one', 'month_many')}")
    lines.append("")

    # No note here about entries we could not date. It is a system
    # caveat in our own vocabulary, and the front door of a family's
    # diary is the wrong place for it. Each affected entry already
    # carries the warning on the page where it is read.
    return "\n".join(lines).rstrip() + "\n"


def home_link(bucket: str) -> str:
    """The diary's pointer, for a page that wants to link to it.

    Both the path and the wording live here, so the linking page needs
    to know neither. `bucket` is the shared bucket the caller prefixes
    every diary path with; see `pages_for`.
    """
    return (f"> [!tip] [{_L['diary_title']}](/{bucket}/{DIARY_DIR}/about)\n"
            f"> {_L['home_teaser']}")


def _and_list(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" {_L['and']} {names[-1]}"


def pages_for(entries, *, room_id: str = "",
              summaries: "dict[str, str] | None" = None,
              ) -> list[tuple[str, str, str]]:
    """Every page the diary publishes: (path, body, title).

    Three levels, because a diary outlives its first year: the root
    names the years, a year names its months, and a month holds the
    entries. Breadcrumbs come free from the path.

    A folder's own page is `about.md`, not `index.md`, matching every
    other entity in this wiki. That convention exists for a reason:
    Quartz serves a folder URL through its folder-page layout, which
    renders no body here, so an `index.md` would be a page whose
    contents nobody can read.

    Paths are relative to the shared bucket, which the caller prefixes
    -- the bucket is named in config (`family`, `office`, a surname)
    and this module has no business knowing which.
    """
    out = [(f"{DIARY_DIR}/about.md", render_index(entries), _L["diary_title"])]
    for year, in_year in sorted(_by_year(entries).items()):
        out.append((
            f"{DIARY_DIR}/{year}/about.md", render_year(in_year), year,
        ))
        for month, in_month in sorted(_by_month(in_year).items()):
            out.append((
                f"{DIARY_DIR}/{year}/{month}.md",
                render_month(in_month, room_id=room_id,
                             summary=(summaries or {}).get(f"{year}-{month}", "")),
                _month_year(in_month[0].on),
            ))
    return out
