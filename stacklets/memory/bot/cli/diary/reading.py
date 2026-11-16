"""The model's reading of each message in the room: step 3 of the diary.

    readings = await read_room(messages, llm, cache)   # event id -> Reading

The model reads the room in overlapping chunks and says, for each
message, the day its words name. The readings are cached by message and
prompt, so a second compile asks the model nothing.
"""

from __future__ import annotations

import json
from datetime import datetime

from stack.ai.client import LLMError

from ... import call_costs
from ...diary.entries import Reading
from .progress import err

# How many messages go to the model at once, and how many of the
# previous chunk to repeat. The overlap exists so a recording split
# across a chunk boundary is still seen whole by one call; a split is
# always two adjacent uploads, so a few messages of run-up is plenty.
_CHUNK = 40
OVERLAP = 4

# One small JSON object per message in the slice, plus the enclosing
# structure. A model that loops instead of closing the array is capped
# here rather than at the client timeout.
_READ_TOKENS_PER_MESSAGE = 120
_READ_TIMEOUT_S = 300.0


READ_PROMPT = """\
You are reading a family's private memories room so their diary can be
compiled. Report facts about these messages. Never translate one. When
you quote, copy the words exactly.

The messages are in the order the server received them, which is not
always the order they were recorded: a phone that has been offline
uploads everything at once when it reconnects.

{messages}

Reply with a JSON object {{"messages": [...]}} holding one object per
message above, in the same order, each with these keys:

"n": the message's number.

"spoken_date": the date the speaker states inside the message, as
  YYYY-MM-DD. Use only a date the text actually names, such as "today is
  March sixteenth". If it names a day and month but no year, choose the
  most recent such date on or before the day the message was received.
  If the text states no date, use null. Never derive one from the
  received date alone.

"mode": a recorded conversation carries no speaker labels, so judge by
  the turns rather than by names. Answer "dialogue" when a statement in
  the text is answered by another statement inside the same text: a
  question and its reply, a claim and its contradiction, someone being
  teased and teasing back. Answer "monologue" when one person speaks
  throughout, however many people they mention or address. Answer "note"
  if it reads as written rather than spoken.

"addressee": who the message is spoken to, exactly as it names them
  ("Bart", "kids"), or null if it is not addressed to anyone in
  particular. In a dialogue both speakers are present, so use null.
"""


def _as_prompt(chunk) -> str:
    """The messages as the reader sees them, numbered from one.

    Numbered rather than keyed by event id because the answers come back
    by number, and a model copying a 43-character Matrix id is a
    transcription test, not a reading one.
    """
    lines = []
    for n, msg in enumerate(chunk, start=1):
        when = datetime.fromtimestamp(
            msg.ts / 1000, msg.zone).strftime("%Y-%m-%d %H:%M")
        kind = {"voice": "voice recording", "image": "photo",
                "video": "video", "file": "file"}.get(msg.kind, "text")
        body = msg.body.strip() or "(no caption)"
        lines.append(f"[{n}] {msg.sender}, received {when}, {kind}:\n{body}")
    return "\n\n".join(lines)


def _chunks(messages):
    """Slices of room history, in arrival order, with a little run-up.

    Arrival order rather than calendar day on purpose: the day a memo
    belongs to is what this pass works out, so it cannot also be what
    decides the batching.
    """
    if len(messages) <= _CHUNK:
        return [list(messages)]
    out, start = [], 0
    while start < len(messages):
        out.append(list(messages[start:start + _CHUNK]))
        start += _CHUNK - OVERLAP
    return out


async def read_room(messages, llm, cache=None):
    """Read the room: what each message says about its day, its speakers
    and whom it is for.

    One call per slice of history rather than one per message: a call
    with no neighbours mistakes a two-person conversation for one person
    reminiscing, and a day named as "yesterday" needs the messages
    around it. Which messages belong together is not asked: what a
    person sends in one go is one entry (`group_room`).

    What the model finds is remembered against the message it describes.
    A message never changes (an edit is a new event pointing at the old
    one), so a message read once is never sent again, and a nightly pass
    costs only what is genuinely new.

    Returns the readings by event id. A slice the model fails or garbles
    contributes nothing and the rest still compiles: the entries are the
    family's words either way, and a missing reading costs a date, not a
    memory.
    """
    readings = _readings_on_file(messages, cache)
    if readings:
        err(f"  {len(readings)} message(s) already read, "
             f"{len(messages) - len(readings)} new")

    slices = _chunks(messages)
    for n, chunk in enumerate(slices, start=1):
        if all(m.event_id in readings for m in chunk):
            continue
        err(f"  reading slice {n} of {len(slices)}")
        rows = await _ask_about(chunk, llm)
        if rows is None:
            continue
        _take(rows, chunk, readings, cache)
        # Saved per slice rather than once at the end, so an
        # interrupted run resumes from the last completed slice.
        if cache is not None:
            cache.save()

    return readings


def _reading_of(row: dict) -> Reading:
    """A reading from one row the model answered, or the cache kept."""
    return Reading(
        mode=str(row.get("mode") or "monologue"),
        spoken_date=row.get("spoken_date") or None,
        addressee=row.get("addressee") or None,
    )


def _readings_on_file(messages, cache) -> "dict[str, Reading]":
    """The readings the cache holds for these messages, by event id."""
    readings: dict[str, Reading] = {}
    if cache is not None:
        for msg in messages:
            if (stored := cache.get(msg.event_id, msg.body)) is not None:
                readings[msg.event_id] = _reading_of(stored)
    return readings


async def _ask_about(chunk, llm) -> "list | None":
    """The model's rows for one slice; None, and said why, when the call
    failed or the answer is not a list of rows."""
    prompt = READ_PROMPT.format(messages=_as_prompt(chunk))
    try:
        raw = await llm.complete(
            "classifier", prompt, json_mode=True, temperature=0,
            max_tokens=len(chunk) * _READ_TOKENS_PER_MESSAGE + 256,
            timeout=_READ_TIMEOUT_S, on_cost=call_costs.recorder("diary-room"))
        payload = json.loads(raw)
        rows = payload.get("messages") if isinstance(payload, dict) else None
    except (LLMError, json.JSONDecodeError, TypeError) as e:
        err(f"  could not read {len(chunk)} message(s): {e}")
        return None
    if not isinstance(rows, list):
        err(f"  unreadable answer for {len(chunk)} message(s)")
        return None
    return rows


def _take(rows: list, chunk, readings: "dict[str, Reading]", cache) -> None:
    """Add the reading of each message a row names, and keep it in the cache.

    A message already read, from the cache or an earlier slice, keeps
    its first reading.
    """
    for row in rows:
        if not isinstance(row, dict):
            continue
        here = _resolve_n(row.get("n"), chunk)
        if here is None or here.event_id in readings:
            continue
        found = {
            "mode": str(row.get("mode") or "monologue"),
            "spoken_date": row.get("spoken_date") or None,
            "addressee": row.get("addressee") or None,
        }
        readings[here.event_id] = _reading_of(found)
        if cache is not None:
            cache.put(here.event_id, found, here.body)


def _resolve_n(value, chunk):
    """The message a returned number points at, or None if it points off
    the end. Models occasionally answer with a number that is not in
    front of them; a reading of thin air is dropped rather than guessed."""
    if not isinstance(value, int) or not 1 <= value <= len(chunk):
        return None
    return chunk[value - 1]
