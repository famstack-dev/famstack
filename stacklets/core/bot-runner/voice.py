"""Decoding voice messages into text before handlers see them.

Speech and typing differ only in encoding, so only the transport needs to
know which one arrived. By the time a handler runs, an `m.audio` event has
become an ordinary text event carrying the same sender, event id and
thread relation, and every gate a typed message passes through applies to
it unchanged.

This is deliberately not a capability that bots invoke. The previous
design made transcription a per-bot concern, which produced an audio
branch in each consumer and three code paths that could transcribe the
same recording. Decoding sits alongside decryption instead: below every
handler, performed once.

The decoded event carries a `dev.famstack.transcript` block recording the
audio it came from. Routing ignores that block; the reply layer reads it
to quote the words back for checking. The audio itself stays on the
timeline and remains the reproducibility anchor (ADR-010).
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


# Marks a text event as decoded from audio, and names the recording.
TRANSCRIPT_KEY = "dev.famstack.transcript"

# Fields describing the audio payload rather than the message. They move
# into the provenance block; left in place, a consumer would read the
# decoded event as an upload and file the bytes a second time.
_PAYLOAD_FIELDS = ("url", "file", "info", "filename")


def is_voice(event) -> bool:
    """Whether `event` is audio this module can decode.

    Any `m.audio` carrying a plain mxc payload qualifies. No attempt is
    made to distinguish a voice memo from a music file first, because
    whisper returning no speech is a more reliable answer than inferring
    the sender's intent from metadata.

    Encrypted media, which carries `file` instead of `url`, is excluded.
    The framework already declines to read encrypted rooms, so claiming
    it here would only produce downloads that fail.
    """
    content = (getattr(event, "source", None) or {}).get("content") or {}
    if content.get("msgtype") != "m.audio":
        return False
    return bool(content.get("url"))


def was_transcribed(content: dict) -> bool:
    """Whether a message's words were transcribed rather than typed.

    Read by the reply layer to decide whether to quote the words back for
    checking. Routing does not consult it: handling speech and typing
    identically is the point of decoding in the transport.
    """
    return isinstance(content.get(TRANSCRIPT_KEY), dict)


def transcribed_source(source: dict, transcript: str) -> dict:
    """Return `source` rewritten as the text event it decodes to.

    Event id, sender, timestamp and thread relation are preserved
    unchanged. Replies, reactions and thread ownership are all keyed on
    that identity, so a synthesised id would detach each of them from the
    message visible in the room.
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

    Transcription costs minutes of GPU for a long recording, and three
    callers arrive at the same message independently: every bot in a room
    drains the same timeline, the drain is at-least-once so a failed
    handler brings its event back, and a backfill walks history in a
    separate process. The store is therefore durable and shared, with one
    file per message written atomically.

    Each record holds the raw whisper output beside the polished text.
    Polishing is cheap and improves with better models; transcription is
    neither, so keeping both allows a later re-polish without returning
    to the audio.
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
        """Write a record, replacing any existing one atomically.

        A store that cannot be written is logged and ignored. The
        transcript still reaches the handler; only the saving is lost, at
        the cost of transcribing again later.
        """
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
        """Return the record for `event_id`, producing it at most once.

        A stored record short-circuits; concurrent callers await the
        first one's result. Failures are not retained: whisper outages
        are transient and the drain retries, so caching an error would
        make a message permanently undecodable.
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
        # Written and published before the in-flight slot is released, so
        # a caller arriving in between finds the result rather than
        # starting a second transcription.
        self.write(event_id, record)
        if not future.done():
            future.set_result(record)
        self._inflight.pop(event_id, None)
        return record


# Process-global: the point is to share across the bots in this runner,
# and with whatever process backfills history.
TRANSCRIPTS = TranscriptStore()
