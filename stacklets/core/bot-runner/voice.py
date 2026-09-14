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

import copy



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


# The store class lives in the shared AI library. All consumers use
# one implementation and one directory: the bots in this runner, the
# diary backfill, and future services. This module keeps the
# process-global instance that the bots import.
from stack.ai.transcripts import TranscriptStore  # noqa: E402,F401

# Process-global: the point is to share across the bots in this runner,
# and with whatever process backfills history.
TRANSCRIPTS = TranscriptStore()
