"""stack memory diary — compile the memories room into the family diary.

The memories room is already full. Somebody has been recording voice
memos to their kids for months, and nothing has ever read them back.
This command is the reader: it walks the room's whole history, decodes
every recording, works out when each one actually happened, and
publishes the result as diary pages in the family wiki.

It is a batch, not a listener. The room is the source of truth and it
only grows, so re-running recompiles the same history into the same
pages rather than appending to something stateful. That makes the
command safe to run again after a bad model day, and it means the
expensive half is cached rather than repeated: transcripts live in
`TRANSCRIPT_DIR`, keyed by event id, shared with the bots.

    stack memory diary                      compile and publish
    stack memory diary --dry-run            print the pages, write nothing
    stack memory diary --room memories      a different room
    stack memory diary --burst-window 1     see below

WHY THE BURST WINDOW IS A KNOB
    Messages that synced late carry arrival timestamps, not recording
    times, and the tell is that they land in a tight run. In a real room
    the gaps between live messages are hours, so 120s separates the two
    cleanly. A *replayed* test corpus compresses those gaps to seconds
    and needs a window well under a second. There is no single value
    that fits both, so the caller picks.

Runs inside `stack-core-bot-runner`: it has the whisper client, the LLM
client, the brain working copy, and the Matrix admin credentials. The
host-side wrapper is a thin docker-exec, like `stack memory wiki`.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # bot/
sys.path.insert(0, "/app")  # voice, stack.ai.client

import diary  # noqa: E402
import voice  # noqa: E402
from stack.ai.client import LLMError, Transcriber  # noqa: E402

from . import wiki  # noqa: E402

HELP = "Compile the memories room into the family diary"

# One page of room history per request. Synapse caps this well above
# 100; the number only trades round trips against response size.
_PAGE = 100


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


# ── Reading the room ──────────────────────────────────────────────────
#
# Through the Synapse admin API, which reads any room without the
# reader having to be a member. A compiler that had to be invited to the
# memories room would be one more thing to set up, and one more account
# with standing access to the most private room in the house.


async def _admin_token(session: aiohttp.ClientSession, homeserver: str) -> str:
    user = os.environ.get("MATRIX_ADMIN_USER", "")
    password = os.environ.get("MATRIX_ADMIN_PASSWORD", "")
    if not user or not password:
        raise RuntimeError("MATRIX_ADMIN_USER/PASSWORD not set in this container")
    async with session.post(f"{homeserver}/_matrix/client/v3/login", json={
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": user},
        "password": password,
    }) as resp:
        if resp.status != 200:
            raise RuntimeError(f"admin login failed: HTTP {resp.status}")
        return (await resp.json())["access_token"]


async def _resolve_room(session, homeserver, token, room: str) -> str:
    if room.startswith("!"):
        return room
    alias = room if room.startswith("#") else \
        f"#{room}:{os.environ.get('MATRIX_SERVER_NAME', '')}"
    async with session.get(
        f"{homeserver}/_matrix/client/v3/directory/room/{quote(alias)}",
        headers={"Authorization": f"Bearer {token}"},
    ) as resp:
        if resp.status != 200:
            raise RuntimeError(f"no such room: {alias}")
        return (await resp.json())["room_id"]


async def _history(session, homeserver, token, room_id: str) -> list[dict]:
    """Every message event in the room, newest page first.

    Paginates backwards until Synapse stops handing back a cursor. The
    caller sorts; order here is only what the API gives us.
    """
    events: list[dict] = []
    cursor = ""
    headers = {"Authorization": f"Bearer {token}"}
    while True:
        url = (f"{homeserver}/_synapse/admin/v1/rooms/{quote(room_id)}"
               f"/messages?dir=b&limit={_PAGE}")
        if cursor:
            url += f"&from={quote(cursor)}"
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                raise RuntimeError(f"could not read room: HTTP {resp.status}")
            payload = await resp.json()
        chunk = payload.get("chunk") or []
        events.extend(chunk)
        cursor = payload.get("end") or ""
        if not chunk or not cursor:
            return events


async def _download(session, homeserver, token, mxc: str) -> bytes | None:
    server, _, media_id = mxc.replace("mxc://", "").partition("/")
    url = (f"{homeserver}/_matrix/client/v1/media/download/"
           f"{quote(server)}/{quote(media_id)}")
    async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as resp:
        if resp.status != 200:
            _err(f"  media {mxc}: HTTP {resp.status}")
            return None
        return await resp.read()


# ── Decoding and reading ──────────────────────────────────────────────


async def _transcribe(message, *, session, homeserver, token,
                      transcriber, llm) -> str:
    """The words of a recording, transcribed once and remembered.

    Shares `TRANSCRIPT_DIR` with the bots, so a memo the archivist
    already heard costs nothing here, and a second compile costs nothing
    at all. That is the whole reason a backfill over a full room is
    affordable.
    """
    async def produce() -> dict:
        audio = await _download(session, homeserver, token, message.url or "")
        if not audio:
            raise LLMError(f"could not download {message.url}")
        raw = await transcriber.transcribe(audio, filename=message.body or "voice.wav")
        text = await Transcriber.polish(raw, llm) if raw.strip() else raw
        return {"raw": raw, "text": text, "url": message.url,
                "filename": message.body}

    try:
        return (await voice.TRANSCRIPTS.run(message.event_id, produce))["text"]
    except LLMError as e:
        _err(f"  could not transcribe {message.event_id}: {e}")
        return ""


_READ_PROMPT = """\
You are reading one message from a family's private memories room so it
can be filed in their diary. Do not rewrite it, summarise it, translate
it, or comment on it. Report only facts about the text as it stands.

This message reached the server on {arrival}.

Message from {sender}:
---
{body}
---

Reply with a JSON object with exactly these keys:

"mode": a recorded conversation carries no speaker labels, so judge by
  the turns rather than by names. Answer "dialogue" when a statement in
  the text is answered by another statement inside the same text: a
  question and its reply, a claim and its contradiction, someone being
  teased and teasing back. Answer "monologue" when one person speaks
  throughout, however many people they mention or address. Answer "note"
  if it reads as written rather than spoken.

"spoken_date": the date the speaker states inside the message, as
  YYYY-MM-DD. Use only a date the text actually names, such as "today is
  March sixteenth". If it names a day and month but no year, choose the
  most recent such date on or before {arrival}. If the text states no
  date at all, use null. Never derive a date from the arrival date
  alone.

"starts_mid_thought": true if the text begins part-way through a
  sentence or thought, as though the recording started late.

"ends_mid_thought": true if the text stops part-way through a sentence
  or thought, as though the recording was cut off.

"addressee": who the message is spoken to, written exactly as the
  message names them ("Bart", "kids", "Maggie"), or null if it is not
  addressed to anyone in particular. Never name the speaker themselves:
  in a conversation between two people who are both present, there is
  no addressee, so use null.
"""


async def _read(message, llm) -> diary.Reading:
    """Ask the model what this message says about itself.

    Temperature 0: the same recording must read the same way on every
    compile, or a rerun would silently reshuffle the diary. A model that
    fails or answers with nonsense yields an empty reading, which dates
    the entry from its timestamp -- worse, but not wrong in a way that
    hides anything.

    Dates, fragment boundaries and addressees come back reliably at this
    model tier. `mode` does not: an unlabelled two-speaker transcript
    reads as one person recounting a conversation, and a 35B model calls
    it a monologue. The prompt is tuned to suppress the false positive
    rather than chase the false negative, because "Conversation" printed
    over a private memo to a child is a worse page than "Voice note"
    printed over a dinner-table recording. Recovering the rest needs
    diarization, which v1 does not have.
    """
    prompt = _READ_PROMPT.format(
        arrival=message.sent_on.isoformat(),
        sender=message.sender,
        body=message.body.strip(),
    )
    try:
        raw = await llm.complete("classifier", prompt,
                                 json_mode=True, temperature=0)
        data = json.loads(raw)
    except (LLMError, json.JSONDecodeError, TypeError) as e:
        _err(f"  could not read {message.event_id}: {e}")
        return diary.Reading()

    if not isinstance(data, dict):
        return diary.Reading()
    return diary.Reading(
        mode=str(data.get("mode") or "monologue"),
        spoken_date=data.get("spoken_date") or None,
        starts_mid_thought=bool(data.get("starts_mid_thought")),
        ends_mid_thought=bool(data.get("ends_mid_thought")),
        addressee=(data.get("addressee") or None),
    )


# ── Recalling a month ─────────────────────────────────────────────────
#
# The one piece of writing on a diary page that is not the family's own.
# It opens a month and it is allowed to be warm, but it may not invent:
# the entries underneath are the record, and a summary that adds to them
# is a lie told about someone's childhood.


_SUMMARY_PROMPT = """\
Write the opening paragraph of a family's diary page for {month}. Below
are that month's entries, quoted exactly as the family recorded them.

Write two to four sentences recalling what happened that month, the way
someone in the family would remember it later.

Rules:
- Use only what the entries say. Never add an event, a feeling, a place
  or an outcome that is not in them.
- Keep every detail with the person the entry keeps it with. Do not move
  something one child did onto another child.
- Keep the direction of what happened. If one person did something for,
  to, or about another, do not swap them round.
- Name people as the entries name them.
- Report what the entries report, and no more. Do not frame the month as
  an occasion, and do not describe an event the entries only mention in
  passing as though the family gathered for it.
- An entry marked "date unknown" happened at no stated time. Do not give
  it one.
- Plain, warm, specific. No marketing words. Never write "heartwarming",
  "cherished", "precious", "journey", "chapter", or a closing sentence
  about what the month meant.
- Return the paragraph and nothing else: no heading, no list, no
  preamble, no quotation marks around it.

Entries:
{evidence}
"""


def _evidence(entries) -> str:
    """A month's entries as the summariser sees them.

    Each line names who recorded it and when, because attribution is the
    thing the model gets wrong: without the date and the sender pinned to
    the words, a summary quietly reassigns a first tooth to the wrong
    child.
    """
    out = []
    for entry in entries:
        when = ("date unknown" if entry.confidence == "uncertain"
                else entry.on.strftime("%-d %B"))
        # Sender only. The addressee is the classifier's reading, and
        # feeding one generation's guess into another compounds it: a
        # nickname misread as a third person becomes a third person in
        # the prose. Whoever a memo is spoken to is named in its words
        # anyway, where the model can read it as evidence.
        who = entry.sender.title()
        body = entry.body.strip() or "(a photo, no caption)"
        for sender, text in entry.comments:
            body += f"\n  {sender.title()} replied: {text.strip()}"
        out.append(f"- [{when}] {who}: {body}")
    return "\n".join(out)


async def _summarise(entries, llm) -> str:
    """A paragraph recalling one month, or "" if the model cannot.

    Temperature 0, so a recompile of an unchanged month reads the same
    way. A failure returns empty and the page falls back to its factual
    opening -- a diary missing its introduction is fine, a diary whose
    introduction changes wording every night is not.
    """
    month = entries[0].on.strftime("%B %Y")
    prompt = _SUMMARY_PROMPT.format(month=month, evidence=_evidence(entries))
    try:
        text = await llm.complete("writer", prompt, temperature=0)
    except LLMError as e:
        _err(f"  could not summarise {month}: {e}")
        return ""
    # A model that answers with a heading or a bulleted list has ignored
    # the brief; the opening is prose or it is nothing.
    cleaned = " ".join(text.strip().split())
    if cleaned.startswith(("#", "-", "*")):
        _err(f"  discarded a non-prose summary for {month}")
        return ""
    return cleaned


# ── The command ───────────────────────────────────────────────────────


def _opt(argv: list[str], flag: str, fallback: str) -> str:
    for i, arg in enumerate(argv):
        if arg == flag and i + 1 < len(argv):
            return argv[i + 1]
    return fallback


async def run(llm, argv: list[str]) -> int:
    room_arg = _opt(argv, "--room", "memories")
    dry_run = "--dry-run" in argv
    try:
        window = float(_opt(argv, "--burst-window",
                            str(diary.DEFAULT_BURST_WINDOW_S)))
    except ValueError:
        _err("--burst-window wants a number of seconds")
        return 2

    homeserver = os.environ.get("MATRIX_HOMESERVER", "").rstrip("/")
    if not homeserver:
        _err("MATRIX_HOMESERVER not set — is core up?")
        return 1
    bucket = os.environ.get("SHARED_BUCKET", "family")

    try:
        transcriber = Transcriber.from_env(namespace="memory-diary")
    except LLMError as e:
        _err(f"no transcription available: {e}")
        return 1

    async with aiohttp.ClientSession() as session:
        try:
            token = await _admin_token(session, homeserver)
            room_id = await _resolve_room(session, homeserver, token, room_arg)
            events = await _history(session, homeserver, token, room_id)
        except (RuntimeError, aiohttp.ClientError) as e:
            _err(str(e))
            return 1

        messages = diary.resolve(events, burst_window_s=window)
        if not messages:
            _err(f"nothing in {room_arg} to compile")
            return 0
        _err(f"{len(messages)} message(s) in {room_arg}")

        # Transcription first and on its own: every later step reads
        # words, and a recording that cannot be decoded should drop out
        # before the model is asked to interpret its filename.
        decoded = []
        for msg in messages:
            if msg.kind != "voice":
                decoded.append(msg)
                continue
            text = await _transcribe(
                msg, session=session, homeserver=homeserver, token=token,
                transcriber=transcriber, llm=llm)
            if not text.strip():
                _err(f"  no speech in {msg.event_id}, skipped")
                continue
            decoded.append(msg.__class__(**{**msg.__dict__, "body": text}))

        readings = {}
        for msg in decoded:
            if msg.kind == "image" or not msg.body.strip():
                readings[msg.event_id] = diary.Reading(mode="note")
                continue
            readings[msg.event_id] = await _read(msg, llm)

    await transcriber.aclose()

    entries = diary.compile_entries(decoded, readings)
    _err(f"{len(entries)} diary entr{'y' if len(entries) == 1 else 'ies'}")

    months: dict[str, list] = {}
    for entry in entries:
        key = f"{diary.year_key(entry.on)}-{diary.month_key(entry.on)}"
        months.setdefault(key, []).append(entry)

    summaries = {}
    for key, in_month in sorted(months.items()):
        summaries[key] = await _summarise(in_month, llm)

    pages = diary.pages_for(entries, room_id=room_id, summaries=summaries)
    if dry_run:
        for path, body, _title in pages:
            print(f"\n{'=' * 70}\n{bucket}/{path}\n{'=' * 70}\n{body}")
        return 0

    rc = 0
    for path, body, title in pages:
        rc |= wiki._publish(
            body, target_path=f"{bucket}/{path}",
            default_preamble=f"---\ntitle: {wiki._yaml_str(title)}\n---",
        )
    return rc


if __name__ == "__main__":  # pragma: no cover - exercised via cli_entrypoint
    raise SystemExit(asyncio.run(run(None, sys.argv[1:])))
