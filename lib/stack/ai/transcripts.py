"""Transcript storage and cleanup passes.

Transcription is expensive and stable. It costs minutes of GPU per
recording, and its output changes only with the whisper configuration
or the vocabulary. The steps after transcription are cheap and depend
on the model: the hallucination gate, punctuation restoration, and
future correction passes. This module separates the two stages.
`TranscriptStore` persists transcription results. Passes process the
stored records and can run again.

A pass is a named, versioned operation on a transcript record. Each
record lists the passes that ran, with their version and model:

    {"raw": ..., "text": ..., "quality": {...},
     "passes": [{"name": "gate", "version": 1, "outcome": "clean"},
                {"name": "polish", "version": 1,
                 "model": "...", "outcome": "applied"}]}

To adopt a better model, increase the pass version. `stale_passes`
finds the records that an older version produced. A sweep then runs
one pass again on the cached raw text. Whisper does not run again.
No pass modifies `raw`. It holds the words as transcribed.

Each consumer selects its own pass chain. Voice commands use
`Transcriber.transcribe` only. The diary uses gate and polish and
keeps the quality metrics. Text messages do not enter this module:
famstack stores them verbatim.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from loguru import logger

from .client import LLMError, Transcriber
from .models import resolve_model


class TranscriptStore:
    """Transcripts on disk, keyed by Matrix event id.

    Transcription costs minutes of GPU for a long recording, and three
    callers arrive at the same message independently: every bot in a room
    drains the same timeline, the drain is at-least-once so a failed
    handler brings its event back, and a backfill walks history in a
    separate process. The store is therefore durable and shared, with one
    file per message written atomically.

    Each record holds the raw whisper output beside the processed text.
    Processing is cheap and improves with better models; transcription is
    neither, so keeping both allows a later re-run of any pass without
    returning to the audio.
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
        *, force: bool = False,
    ) -> dict:
        """Return the record for `event_id`, producing it at most once.

        A stored record short-circuits; concurrent callers await the
        first one's result. Failures are not retained: whisper outages
        are transient and the drain retries, so caching an error would
        make a message permanently undecodable.

        ``force`` skips the stored record and re-produces it — the
        `--retranscribe` path for when the whisper config or vocabulary
        improved and old recordings deserve a second hearing. The new
        record overwrites the old one; single-flight still applies.
        """
        if not force and (stored := self.read(event_id)) is not None:
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


# ── Cleanup passes ────────────────────────────────────────────────────


@dataclass(frozen=True)
class TranscriptPass:
    """One named, versioned operation on a transcript record.

    ``apply`` receives the record and returns (text, outcome, extra).
    ``text`` is the new text, or the old text if unchanged. ``outcome``
    is a short status for the pass list: "applied", "clean",
    "blocked: <reason>". ``extra`` holds metadata to keep, for example
    the model name. The runner writes the pass list. A pass makes one
    decision.
    """

    name: str
    version: int
    apply: Callable[[dict], Awaitable[tuple[str, str, dict]]]


async def run_passes(record: dict, passes: list[TranscriptPass]) -> dict:
    """Run a pass chain on a record and update its pass list.

    A pass that raises an LLMError is logged and recorded as failed.
    The chain continues with the unchanged text. A broken cleanup pass
    does not remove the transcript. No pass modifies ``raw``.
    """
    trail = list(record.get("passes") or [])
    for p in passes:
        try:
            text, outcome, extra = await p.apply(record)
            record["text"] = text
        except LLMError as e:
            outcome, extra = f"failed: {e}", {}
            logger.warning("[transcripts] pass {} failed: {}", p.name, e)
        trail = [e for e in trail if e.get("name") != p.name]
        trail.append({"name": p.name, "version": p.version,
                      "outcome": outcome, **extra})
    record["passes"] = trail
    return record


def stale_passes(record: dict, passes: list[TranscriptPass]) -> list[TranscriptPass]:
    """Return the passes with a missing or outdated entry in the record.

    A sweep uses this to run only the passes that a version change
    invalidated. The input is the cached raw text. Whisper does not
    run again.
    """
    ran = {e.get("name"): e.get("version")
           for e in (record.get("passes") or [])}
    return [p for p in passes if ran.get(p.name) != p.version]


def gate_pass() -> TranscriptPass:
    """Block hallucinated transcripts before a model receives them.

    The gate uses two independent signals. Whisper's segment metrics
    detect low-confidence failures: unclear speech and silence. The
    text checks detect high-confidence failures: repetition loops and
    CJK output for a latin-language household. A blocked record gets
    empty text. The consumer keeps the entry and its audio link.
    """
    async def apply(record: dict) -> tuple[str, str, dict]:
        raw = (record.get("raw") or "").strip()
        if not raw:
            return "", "clean", {}
        why = (Transcriber.quality_verdict(record.get("quality") or {})
               or Transcriber.looks_degenerate(raw))
        if why:
            return "", f"blocked: {why}", {}
        return record.get("text") or raw, "clean", {}

    return TranscriptPass(name="gate", version=1, apply=apply)


def structure_pass(min_pause_s: float = 1.5,
                   min_words: int = 8) -> TranscriptPass:
    """Insert paragraph breaks at long pauses between segments.

    Whisper's segment boundaries fall on pauses in the speech. A pause
    of ``min_pause_s`` or more starts a new paragraph. Paragraphs
    shorter than ``min_words`` merge into the next one. The split uses
    the per-segment word counts from the quality record; no model runs.

    Polish can merge or split words (hyphenation), which shifts the
    counts. When the counts do not match the text, the pass skips and
    the text stays unchanged. Run this pass after polish.
    """
    async def apply(record: dict) -> tuple[str, str, dict]:
        text = (record.get("text") or "").strip()
        segments = (record.get("quality") or {}).get("segments") or []
        counts = [s.get("word_count") for s in segments]
        if not text or not segments or None in counts:
            return record.get("text") or "", "skipped: no segment data", {}
        words = text.split()
        if sum(counts) != len(words):
            return text, "skipped: word counts do not match", {}

        paragraphs: list[str] = []
        current: list[str] = []
        cursor = 0
        for i, segment in enumerate(segments):
            current.extend(words[cursor:cursor + counts[i]])
            cursor += counts[i]
            following = segments[i + 1] if i + 1 < len(segments) else None
            pause = 0.0
            if (following and segment.get("end") is not None
                    and following.get("start") is not None):
                pause = following["start"] - segment["end"]
            if following is None or (pause >= min_pause_s
                                     and len(current) >= min_words):
                paragraphs.append(" ".join(current))
                current = []
        if current:
            paragraphs.append(" ".join(current))
        structured = "\n\n".join(paragraphs)
        outcome = "applied" if len(paragraphs) > 1 else "unchanged"
        return structured, outcome, {}

    return TranscriptPass(name="structure", version=1, apply=apply)


def polish_pass(llm) -> TranscriptPass:
    """Restore punctuation. Do not change words (see Transcriber.polish).

    The pass skips records with empty text. The gate empties the text
    on purpose; an empty input is not a failure.
    """
    async def apply(record: dict) -> tuple[str, str, dict]:
        text = (record.get("text") or "").strip()
        if not text:
            return record.get("text") or "", "skipped: no text", {}
        polished = await Transcriber.polish(text, llm)
        outcome = "applied" if polished != text else "unchanged"
        # The model name is metadata. A missing configuration must not
        # fail the pass.
        try:
            extra = {"model": resolve_model("transcript_cleanup")}
        except ValueError:
            extra = {}
        return polished, outcome, extra

    return TranscriptPass(name="polish", version=1, apply=apply)
