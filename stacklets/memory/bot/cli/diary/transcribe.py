"""The words of each recording, decoded once and remembered.

    decoded = await decode_room(messages, session=session, homeserver=homeserver,
                                 token=token, transcriber=transcriber, llm=llm,
                                 retranscribe=False)

Whisper decodes a recording against the household's vocabulary, then a
chain of passes cleans the text (`stack.ai.transcripts`): a gate that
blocks hallucinated words, punctuation, the household's names, the
paragraphs. The record is shared with the bots (`TRANSCRIPT_DIR`), so a
memo the archivist already heard costs nothing here.
"""

from __future__ import annotations

from stack.ai import transcripts
from stack.ai.client import LLMError
from stack.ai.transcripts import TRANSCRIPTS

from ... import call_costs
from stack.household_people import household_people

from .household_files import household_vocabulary
from .progress import err
from .room import download


def _length(ms: int | None) -> str:
    """Duration as m:ss, for the progress line."""
    if not ms:
        return "length unknown"
    total = round(ms / 1000)
    return f"{total // 60}:{total % 60:02d}"


def _pass_chain(llm, people: list | None):
    return [transcripts.gate_pass(),
            transcripts.polish_pass(llm, on_cost=call_costs.recorder("transcript-polish")),
            transcripts.correct_pass(llm, people or [],
                                     on_cost=call_costs.recorder("transcript-names")),
            transcripts.structure_pass()]


async def _transcribe(message, *, session, homeserver, token,
                      transcriber, llm, vocabulary: str = "",
                      people: list | None = None,
                      retranscribe: bool = False) -> str:
    """The words of a recording, transcribed once and remembered.

    Shares `TRANSCRIPT_DIR` with the bots, so a memo the archivist
    already heard costs nothing here, and a second compile costs nothing
    at all. That is the whole reason a backfill over a full room is
    affordable.
    """
    chain = _pass_chain(llm, people)

    async def produce() -> dict:
        return await _decode(message, chain, session=session, homeserver=homeserver,
                             token=token, transcriber=transcriber, vocabulary=vocabulary)

    try:
        record = await TRANSCRIPTS.run(
            message.event_id, produce, force=retranscribe)
        return (await _with_current_passes(message, record, chain))["text"]
    except LLMError as e:
        err(f"  could not transcribe {message.event_id}: {e}")
        return ""


async def _decode(message, chain, *, session, homeserver, token, transcriber,
                  vocabulary: str) -> dict:
    """A new transcript record of `message`: whisper's text, then the passes.

    Raises `LLMError` when the recording cannot be downloaded.
    """
    audio = await download(session, homeserver, token, message.url or "")
    if not audio:
        raise LLMError(f"could not download {message.url}")
    verbose = await transcriber.transcribe_verbose(
        audio, filename=message.body or "voice.wav", vocabulary=vocabulary)
    record = {"raw": verbose["text"], "text": verbose["text"],
              "quality": verbose["quality"],
              "url": message.url, "filename": message.body}
    # The diary's cleanup chain. The gate blocks hallucinated
    # words; the entry keeps its place and its audio. Polish
    # restores punctuation. The record lists each pass, so a
    # better future model can run one pass again on the cached
    # raw text. Whisper does not run again.
    record = await transcripts.run_passes(record, chain)
    gate = next((p for p in record["passes"] if p["name"] == "gate"), {})
    if str(gate.get("outcome", "")).startswith("blocked"):
        err(f"  transcript of {message.event_id} unusable "
             f"({gate['outcome']}); keeping the recording without words")
    return record


async def _with_current_passes(message, record: dict, chain) -> dict:
    """`record`, with the passes run again from its raw text when one changed.

    A cached record skipped `_decode` and with it the passes. A changed
    pass fingerprint (prompt edit, new household member, version bump)
    re-runs the whole chain from raw text. Whisper does not run again.
    """
    if stale := transcripts.stale_passes(record, chain):
        err(f"  passes changed for {message.event_id}: "
             + ", ".join(p.name for p in stale))
        record = dict(record)
        record["text"] = record.get("raw") or ""
        record = await transcripts.run_passes(record, chain)
        TRANSCRIPTS.write(message.event_id, record)
    return record


# ── The room's recordings ─────────────────────────────────────────────


async def decode_room(messages, *, session, homeserver, token, transcriber, llm,
                       retranscribe: bool) -> list:
    """The room's messages with each recording's words as its body.

    Transcription comes first and on its own: every later step reads
    words, and a recording that cannot be decoded should drop out before
    the model is asked to interpret its filename. A recording whisper
    heard nothing in is dropped; one whose words the gate refused keeps
    its place and its audio, with no words.
    """
    vocabulary = household_vocabulary()
    people = household_people()
    if vocabulary:
        err(f"  decoding against: {vocabulary[:90]}...")

    # Transcription dominates runtime, so each recording is logged
    # before the whisper call rather than after. Cached transcripts
    # return immediately and are not logged.
    recordings = [m for m in messages if m.kind == "voice"]
    pending = sum(1 for m in recordings
                  if TRANSCRIPTS.read(m.event_id) is None)
    if recordings:
        err(f"  {len(recordings)} recording(s), {pending} to decode")

    decoded, heard = [], 0
    for msg in messages:
        if msg.kind != "voice":
            decoded.append(msg)
            continue
        heard += 1
        if TRANSCRIPTS.read(msg.event_id) is None:
            err(f"  [{heard}/{len(recordings)}] decoding {msg.sender}, "
                 f"{_length(msg.duration_ms)}")
        text = await _transcribe(
            msg, session=session, homeserver=homeserver, token=token,
            transcriber=transcriber, llm=llm, vocabulary=vocabulary,
            people=people, retranscribe=retranscribe)
        if (with_words := _with_words(msg, text)) is not None:
            decoded.append(with_words)
    return decoded


def _with_words(msg, text: str):
    """The recording `msg` with `text` as its body; None for one whisper
    heard no speech in."""
    if text.strip():
        return msg.__class__(**{**msg.__dict__, "body": text})
    record = TRANSCRIPTS.read(msg.event_id) or {}
    if (record.get("raw") or "").strip():
        # Whisper heard something but the gate judged it
        # unusable (loop, CJK, failed segments). The memory
        # is not dropped: the entry keeps its place and its
        # audio, with no words attached.
        return msg.__class__(**{**msg.__dict__, "body": ""})
    err(f"  no speech in {msg.event_id}, skipped")
    return None
