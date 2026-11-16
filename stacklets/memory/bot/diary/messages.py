"""The room's events as messages: step 1 of the diary.

    messages = resolve(events, zone=ZoneInfo("Europe/Berlin"))
    kept_paths(entry.event_ids, media)    # the files kept for an entry

Pure Matrix mechanics, no reading of meaning. Edits collapse onto the
message they replace, reply fallbacks come off the front of bodies, a
gallery becomes one message with its files, and a reply or a thread
names the message it adds to. Nothing here needs a model, and getting
it wrong corrupts every later step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone, tzinfo

# ── What the room gives us ────────────────────────────────────────────


@dataclass(frozen=True)
class MediaFile:
    """One file of a gallery: what the archive needs to fetch and keep it.

    `artifact_id` names the file in the media archive, so two files of one
    gallery never share a name (`gallery_artifact_id`).
    """

    artifact_id: str
    kind: str  # "image" | "video" | "voice" | "file"
    url: str | None
    duration_ms: int | None = None
    mime: str = ""
    filename: str = ""


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
    zone: tzinfo = timezone.utc
    # The first message of the thread this one was written in, if any.
    thread_root: str | None = None
    # The files of a gallery, in the order it lists them. Empty for any
    # other message; `files` is the one place that answers "which files".
    gallery: tuple[MediaFile, ...] = ()
    # The earliest day its files were taken, when one kept it (EXIF).
    taken: date | None = None

    @property
    def parent(self) -> str | None:
        """The message this one adds to: its thread's first message, else
        the message it replies to, else None. A message group reads it
        (`stack.message_groups.Groupable`): a message with a parent joins
        the entry of that message, whenever it comes."""
        return self.thread_root or self.reply_to

    @property
    def files(self) -> tuple[MediaFile, ...]:
        """Every file this message carries, in order.

        A gallery lists its own; an upload is its one file under its event
        id; a text message has none.
        """
        if self.gallery:
            return self.gallery
        if self.kind == "text" or not self.url:
            return ()
        return (MediaFile(self.event_id, self.kind, self.url, self.duration_ms),)

    @property
    def sent_on(self) -> date:
        """The calendar day the family would say this happened on.

        Read in the household's timezone, not UTC. A memo recorded at
        half past midnight in Berlin is a UTC message from the previous
        day, and filing it there puts it under the wrong heading in a
        diary whose whole job is saying when things happened.
        """
        return datetime.fromtimestamp(self.ts / 1000, self.zone).date()


# ── Step 1: resolve ───────────────────────────────────────────────────
#
# Pure Matrix mechanics, no reading of meaning. Edits collapse onto the
# message they replace, reply fallbacks come off the front of bodies,
# and a reply or a thread names the message it adds to. Nothing here
# needs a model, and getting it wrong corrupts every later step.


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


# A gallery is one message with several files (MSC4274). Element X sends
# the unstable prefix while the proposal is open.
_GALLERIES = ("m.gallery", "dm.filament.gallery")

# What an item of a gallery is, by its `itemtype` (the schema of the
# message type of the same name).
_ITEM_KINDS = {"m.audio": "voice", "m.image": "image", "m.video": "video",
               "m.file": "file"}


def gallery_artifact_id(event_id: str, place: int) -> str:
    """The archive name of a gallery's file: its event and its place, from 1."""
    return f"{event_id}-{place}"


def kept_paths(event_ids, media: "dict[str, str]") -> list[str]:
    """The archived files of these messages, in order.

    `media` is keyed by artifact id: an upload's is its event id, a
    gallery's file's is `gallery_artifact_id`. A gallery has no entry under
    its own event id, so its files are found by place until one is missing.
    """
    paths = []
    for event_id in event_ids:
        if event_id in media:
            paths.append(media[event_id])
            continue
        place = 1
        while (path := media.get(gallery_artifact_id(event_id, place))):
            paths.append(path)
            place += 1
    return paths


def _gallery_files(event_id: str, content: dict) -> tuple[MediaFile, ...]:
    """The files of a gallery event, in order; items of an unknown type are skipped."""
    files = []
    for item in content.get("itemtypes") or []:
        kind = _ITEM_KINDS.get(item.get("itemtype", ""))
        if kind is None or not item.get("url"):
            continue
        info = item.get("info") or {}
        files.append(MediaFile(
            artifact_id=gallery_artifact_id(event_id, len(files) + 1),
            kind=kind, url=item["url"], duration_ms=info.get("duration"),
            mime=info.get("mimetype") or "",
            filename=item.get("filename") or item.get("body") or ""))
    return tuple(files)


# Bot accounts are named by convention: a localpart ending in `-bot`.
# The framework owns that definition (`MicroBot.is_bot_user`), which we
# cannot import here without pulling a Matrix client into a module that
# is pure on purpose. What a bot posts in the room is instruction, not
# memory, and a diary that opens with the welcome message opens with
# someone else's words.
_BOT_SUFFIX = "-bot"


def _total_ms(files) -> int | None:
    durations = [f.duration_ms for f in files if f.duration_ms]
    return sum(durations) if durations else None


def resolve(events, *, zone: tzinfo = timezone.utc):
    """Room events to messages: edits applied, replies linked.

    `events` is the raw chunk from Synapse in any order; the result is
    oldest-first. Redacted events arrive with an empty content dict and
    are dropped -- the message is gone, and a diary that renders a
    tombstone is worse than one that renders nothing.
    """
    edits: dict[str, tuple[int, str]] = {}
    plain: list[Message] = []
    for ev in events:
        read = _read_event(ev, zone=zone)
        if isinstance(read, Message):
            plain.append(read)
        elif read is not None:
            _keep_latest(edits, *read)

    plain.sort(key=lambda m: (m.ts, m.event_id))
    return [
        replace(m, body=edits[m.event_id][1]) if m.event_id in edits else m
        for m in plain
    ]


def _read_event(ev, *, zone: tzinfo) -> "Message | tuple[str, int, str] | None":
    """One event as a message, as an edit (the event it edits, when, the
    new body), or None for an event that is neither: another type, a
    bot's message, a message type the diary does not keep."""
    if ev.get("type") != "m.room.message":
        return None
    sender = (ev.get("sender") or "").split(":")[0].lstrip("@")
    if sender.endswith(_BOT_SUFFIX):
        return None
    content = ev.get("content") or {}
    msgtype = content.get("msgtype", "")
    gallery = (_gallery_files(ev.get("event_id", ""), content)
               if msgtype in _GALLERIES else ())
    # A gallery is as many kinds as it has files; its entry is the
    # first file's.
    kind = gallery[0].kind if gallery else _kind_of(msgtype)
    if kind is None:
        return None

    relates = content.get("m.relates_to") or {}
    ts = ev.get("origin_server_ts") or 0
    # An edit is not a message. It is a correction to one, and only
    # the final version counts.
    if relates.get("rel_type") == "m.replace":
        return (relates.get("event_id") or "", ts,
                (content.get("m.new_content") or {}).get("body", ""))
    return _message(ev, content, relates, sender=sender, kind=kind, gallery=gallery,
                    ts=ts, zone=zone)


def _keep_latest(edits: "dict[str, tuple[int, str]]", target: str, ts: int, body: str) -> None:
    """Keep the edit of `target` when it is the latest one so far."""
    if target and body and ts >= edits.get(target, (0, ""))[0]:
        edits[target] = (ts, body)


def _message(ev, content: dict, relates: dict, *, sender: str, kind: str, gallery,
             ts: int, zone: tzinfo) -> Message:
    """The message an event that is no edit carries."""
    info = content.get("info") or {}
    if gallery:
        # The gallery's `body` is its caption already; the file names
        # are on the items.
        info = {"duration": _total_ms(gallery)}
    in_reply_to, thread_root = _relation(relates)
    return Message(
        event_id=ev.get("event_id", ""),
        sender=sender,
        ts=ts,
        kind=kind,
        body=_body(content, kind=kind, gallery=gallery, in_reply_to=in_reply_to),
        url=gallery[0].url if gallery else content.get("url"),
        duration_ms=info.get("duration"),
        reply_to=in_reply_to,
        zone=zone,
        thread_root=thread_root,
        gallery=gallery,
    )


def _relation(relates: dict) -> "tuple[str | None, str | None]":
    """The message an event replies to and the thread it is in."""
    in_reply_to = (relates.get("m.in_reply_to") or {}).get("event_id")
    # In a thread, a client adds `m.in_reply_to` pointing at the
    # thread's latest event so older clients can show something.
    # The thread is the relation that means something here.
    thread_root = (relates.get("event_id")
                   if relates.get("rel_type") == "m.thread" else None)
    if thread_root:
        in_reply_to = None
    return in_reply_to, thread_root


def _body(content: dict, *, kind: str, gallery, in_reply_to: "str | None") -> str:
    """The words of a message: its body without the reply fallback, and
    no filename where a caption belongs."""
    body = content.get("body", "")
    if in_reply_to:
        body = strip_reply_fallback(body)
    if kind in _UPLOADS and not gallery:
        # `body` is the filename until a caption displaces it, at
        # which point the name moves to `filename`. Clients that set
        # `filename` to the same string are still sending a bare
        # upload, so compare rather than test for presence -- else
        # the diary prints "IMG_4021.png" where a caption belongs.
        filename = content.get("filename")
        body = body if (filename and filename != body) else ""
    return body
