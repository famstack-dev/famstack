"""Voice messages are messages. The transport decodes them, not the bots.

Someone holding the mic button and someone typing are doing the same
thing: putting words in the room. Only the encoding differs, so only the
transport should know about it. By the time a handler runs, an `m.audio`
event has already become an ordinary text event — same sender, same event
id, same thread — and every gate a typed message passes through (room
mode, thread ownership, mention, routing) applies to it unchanged.

This is deliberately not a capability bots reach for. That was the earlier
design, and it grew an audio branch in every consumer, two whisper clients
in one process, and three places that could transcribe the same bytes.
Decoding belongs beside decryption: below everyone, done once.

What survives the decode is provenance. Whisper is lossy in a way a
keyboard is not, so the text event carries a `dev.famstack.transcript`
block naming the audio it came from. Routing ignores it; the bot that
acts on the words uses it to echo them back, which is the only way a
mistranscription is visible without opening the vault. The audio itself
stays on the timeline as the reproducibility anchor (ADR-010) — we never
need to keep a second copy of the bytes.

The module owns the pure half: recognising a voice event and rewriting its
raw source dict. `MicroBot._dispatch` owns the I/O half (download,
whisper, share).
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from loguru import logger


# Provenance on a decoded message: which audio event these words came
# from. Its presence is the answer to "were these words guessed at by a
# machine?" — a question the reply layer asks and the routing layer must not.
TRANSCRIPT_KEY = "dev.famstack.transcript"

# Fields of the audio event that describe the payload rather than the
# message. They move into the provenance block; leaving them on a text
# event would let a consumer treat it as an upload and re-file the bytes.
_PAYLOAD_FIELDS = ("url", "file", "info", "filename")


def is_voice(event) -> bool:
    """Whether this timeline event is speech we can decode.

    Any `m.audio` with a plain mxc payload counts. We do not try to tell
    a voice memo from a music file first: whisper answering "no words
    here" is a better detector than a guess at the sender's intent, and
    the clients families use mark both the same way.

    Encrypted media (`file` rather than `url`) is deliberately not
    claimed. The framework already tells encrypted rooms it cannot read
    them, so saying yes here would only produce a download that fails.
    """
    content = (getattr(event, "source", None) or {}).get("content") or {}
    if content.get("msgtype") != "m.audio":
        return False
    return bool(content.get("url"))


def was_transcribed(content: dict) -> bool:
    """Whether a message's words were transcribed rather than typed.

    Reads the framework contract, so it works for any consumer without
    knowing which component decoded the audio. Consumers ask this to
    decide whether to show the words back for checking, never to decide
    routing — routing is the whole thing that must not care.
    """
    return isinstance(content.get(TRANSCRIPT_KEY), dict)


def transcribed_source(source: dict, transcript: str) -> dict:
    """Rewrite a voice event's raw source dict as the text event it is.

    Identity is preserved wholesale — event id, sender, timestamp,
    thread relation — because this is the same message, read aloud
    rather than typed. Bots reply to it, react on it and claim thread
    ownership of it by that identity, so a synthesised id would detach
    every one of those from the message the family can actually see.
    """
    out = copy.deepcopy(source)
    content = out.setdefault("content", {})

    provenance = {"source_msgtype": content.get("msgtype", "m.audio")}
    if url := content.get("url"):
        provenance["url"] = url
    if filename := (content.get("filename") or content.get("body")):
        provenance["filename"] = filename
    info = content.get("info")
    if isinstance(info, dict):
        if (duration := info.get("duration")) is not None:
            provenance["duration"] = duration
        if mimetype := info.get("mimetype"):
            provenance["mimetype"] = mimetype

    for field in _PAYLOAD_FIELDS:
        content.pop(field, None)
    content["msgtype"] = "m.text"
    content["body"] = transcript
    content[TRANSCRIPT_KEY] = provenance
    return out


class TranscriptStore:
    """Transcripts on disk, keyed by Matrix event id.

    Transcription is the most expensive thing the stack does per message:
    minutes of GPU for a long memo. Three callers want the same answer and
    must not each pay for it.

      * Every bot in the room drains the same timeline in the same
        process, so each reaches the same audio independently.
      * The drain is at-least-once, so a handler that dies mid-flight
        brings its event back around.
      * A backfill walks a room's whole history in a *separate* process.
        The memories room holds years of recordings; re-transcribing that
        because the answer was only ever in RAM is not acceptable once,
        let alone every time something wants to read it.

    So the store is durable and shared, and the in-flight map on top of it
    collapses concurrent askers onto one run. One file per voice message,
    written atomically, because the backfill and the bot runner are
    different processes writing the same directory.

    Each record keeps the raw whisper output next to the polished text.
    Polishing is cheap and improves with better models; whisper is not and
    does not. Keeping both means years of recordings can be re-polished
    without touching the audio again.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(
            path or os.environ.get("TRANSCRIPT_DIR", "/data/core/transcripts")
        )
        self._inflight: dict[str, asyncio.Future] = {}

    # ── Durable half ─────────────────────────────────────────────────

    def _file(self, event_id: str) -> Path:
        # Event ids carry `$`, `/` and `+`; base64url keeps one file per
        # id without inventing a collision-prone slug.
        name = base64.urlsafe_b64encode(event_id.encode()).decode().rstrip("=")
        return self.path / f"{name}.json"

    def read(self, event_id: str) -> dict | None:
        """The stored record for `event_id`, or None if we never ran it."""
        try:
            return json.loads(self._file(event_id).read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            logger.warning("[voice] unreadable transcript for {}: {}", event_id, e)
            return None

    def write(self, event_id: str, record: dict) -> None:
        """Persist a record. A store we cannot write is not fatal — the
        transcript still reaches the handler, it just costs again later."""
        target = self._file(event_id)
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2))
            os.replace(tmp, target)
        except OSError as e:
            logger.warning("[voice] could not store transcript {}: {}", event_id, e)

    # ── Single-flight half ───────────────────────────────────────────

    async def run(
        self, event_id: str, produce: Callable[[], Awaitable[dict]],
    ) -> dict:
        """The record for `event_id`, produced at most once across callers.

        A failure is never remembered. The drain is at-least-once and
        whisper outages are transient, so caching an error would turn a
        restartable service into a permanently silent message.
        """
        if (stored := self.read(event_id)) is not None:
            return stored
        if (pending := self._inflight.get(event_id)) is not None:
            return await pending

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._inflight[event_id] = future
        try:
            record = await produce()
        except BaseException as e:
            self._inflight.pop(event_id, None)
            if not future.done():
                future.set_exception(e)
            # Retrieve it so an unawaited future does not warn.
            future.exception()
            raise
        record.setdefault("at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        # Publish before releasing the slot, so a caller arriving in
        # between finds the answer rather than starting a second run.
        self.write(event_id, record)
        if not future.done():
            future.set_result(record)
        self._inflight.pop(event_id, None)
        return record


# Process-global: the point is to share across the bots in this runner,
# and with whatever process backfills history.
TRANSCRIPTS = TranscriptStore()
