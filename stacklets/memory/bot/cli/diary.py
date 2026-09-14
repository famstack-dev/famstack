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
    stack memory diary --dry-run            print the pages, publish nothing
    stack memory diary letters              a different room
    stack memory diary --burst-window 1     see below
    stack memory diary --force              re-read everything from scratch
    stack memory diary --retranscribe       decode all recordings again
                                            (after a whisper config or
                                            vocabulary change)
    stack memory diary --limit 20           only the newest 20 messages
                                            (bounded preview)

WHY THE BURST WINDOW IS A KNOB
    Messages that synced late carry arrival timestamps, not recording
    times, and the tell is that they land in a tight run. In a real room
    the gaps between live messages are hours, so 120s separates the two
    cleanly. A *replayed* test corpus compresses those gaps to seconds
    and needs a window well under a second. There is no single value
    that fits both, so the caller picks.

A dry run publishes no pages but still writes the transcript, reading
and summary caches. These are cost caches, not output: discarding them
would make a preview run as expensive as the compile that follows.

Runs inside `stack-core-bot-runner`: it has the whisper client, the LLM
client, the brain working copy, and the Matrix admin credentials. The
host-side wrapper is a thin docker-exec, like `stack memory wiki`.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# httpx rather than aiohttp: the OpenAI SDK already pulls it into every
# container that can talk to a model, so the compiler runs unchanged in
# the bot-runner and in the curator that schedules it nightly.
import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # bot/
sys.path.insert(0, "/app")  # stack.ai.client, and voice in the bot-runner
# The transcript store lives with the bot-runner's voice module. The
# bot-runner has it baked at /app; the curator, which schedules this
# nightly, only mounts /stacklets. Both see it here.
sys.path.append("/stacklets/core/bot-runner")

import diary  # noqa: E402
import diary_store  # noqa: E402
import voice  # noqa: E402
from stack.ai.client import LLMError, Transcriber  # noqa: E402
from stack.ai import transcripts  # noqa: E402

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


async def _admin_token(session: httpx.AsyncClient, homeserver: str) -> str:
    user = os.environ.get("MATRIX_ADMIN_USER", "")
    password = os.environ.get("MATRIX_ADMIN_PASSWORD", "")
    if not user or not password:
        raise RuntimeError("MATRIX_ADMIN_USER/PASSWORD not set in this container")
    resp = await session.post(f"{homeserver}/_matrix/client/v3/login", json={
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": user},
        "password": password,
    })
    if resp.status_code != 200:
        raise RuntimeError(f"admin login failed: HTTP {resp.status_code}")
    return resp.json()["access_token"]


async def _resolve_room(session, homeserver, token, room: str) -> str:
    if room.startswith("!"):
        return room
    alias = room if room.startswith("#") else \
        f"#{room}:{os.environ.get('MATRIX_SERVER_NAME', '')}"
    resp = await session.get(
        f"{homeserver}/_matrix/client/v3/directory/room/{quote(alias)}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"no such room: {alias}")
    return resp.json()["room_id"]


async def _history(session, homeserver, token, room_id: str,
                   limit: int = 0) -> list[dict]:
    """Message events in the room, newest page first.

    Paginates backwards until Synapse stops handing back a cursor. A
    positive ``limit`` stops after that many events: the newest part
    of the room, for a bounded preview run. The caller sorts; order
    here is only what the API gives us.

    Counts out loud as it goes. A room with years in it takes many
    round trips before anything else can start, and a command that
    prints nothing for that long is indistinguishable from one that has
    hung.
    """
    events: list[dict] = []
    cursor = ""
    headers = {"Authorization": f"Bearer {token}"}
    while True:
        url = (f"{homeserver}/_synapse/admin/v1/rooms/{quote(room_id)}"
               f"/messages?dir=b&limit={_PAGE}")
        if cursor:
            url += f"&from={quote(cursor)}"
        resp = await session.get(url, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"could not read room: HTTP {resp.status_code}")
        payload = resp.json()
        chunk = payload.get("chunk") or []
        events.extend(chunk)
        if chunk:
            _err(f"  read {len(events)} events so far")
        cursor = payload.get("end") or ""
        if limit and len(events) >= limit:
            return events[:limit]
        if not chunk or not cursor:
            return events


async def _download(session, homeserver, token, mxc: str) -> bytes | None:
    server, _, media_id = mxc.replace("mxc://", "").partition("/")
    url = (f"{homeserver}/_matrix/client/v1/media/download/"
           f"{quote(server)}/{quote(media_id)}")
    resp = await session.get(url, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code != 200:
        _err(f"  media {mxc}: HTTP {resp.status_code}")
        return None
    return resp.content


# ── Decoding and reading ──────────────────────────────────────────────


# Whisper's decoder prompt is a small window (a couple of hundred
# tokens); past it the hint is truncated from the front, which would
# drop the names silently. Names first, topics only with room to spare.
_VOCAB_BUDGET = 600


def _length(ms: int | None) -> str:
    """Duration as m:ss, for the progress line."""
    if not ms:
        return "length unknown"
    total = round(ms / 1000)
    return f"{total // 60}:{total % 60:02d}"


def _household_vocabulary() -> str:
    """The names and subjects this family uses, for whisper to decode against.

    People come from the wiki's person pages, which already carry the
    household's own spelling of each name and any variants it uses. The
    ontology's topics follow, because a family's proper nouns are not
    only its people -- a campsite, a school, a pet -- and those mishear
    just as readily.

    Best-effort: a vault that has not been generated yet simply yields
    nothing, and transcription proceeds exactly as it did before.
    """
    people: list[str] = []
    brain = Path(os.environ.get("BRAIN_REPO_DIR", ""))
    if brain.is_dir():
        for about in sorted(brain.glob("*/about.md")):
            front = _frontmatter(about)
            if front.get("type") != "person":
                continue
            for key in ("canonical", "title"):
                if value := front.get(key):
                    people.append(str(value))
                    break
            synonyms = front.get("synonyms")
            if isinstance(synonyms, list):
                people.extend(str(x) for x in synonyms)

    topics: list[str] = []
    vault = Path(os.environ.get("MEMORY_VAULT_DIR", ""))
    lang = os.environ.get("LANGUAGE", "en")
    try:
        from stack.ontology import Ontology
        loaded = Ontology.load(vault / "ontology.toml")
        for topic in loaded.topics.values():
            topics.append(topic.name(lang))
            topics.extend(topic.synonyms_for(lang))
    except Exception as e:  # noqa: BLE001 - a hint is never worth failing over
        _err(f"  no ontology for the transcript hint: {e}")

    hint = diary.spoken_vocabulary(people, topics)
    if len(hint) <= _VOCAB_BUDGET:
        return hint
    # Over budget: keep the people, who are what mishears most.
    return diary.spoken_vocabulary(people)[:_VOCAB_BUDGET]


def _frontmatter(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    try:
        loaded = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


async def _transcribe(message, *, session, homeserver, token,
                      transcriber, llm, vocabulary: str = "",
                      retranscribe: bool = False) -> str:
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
        record = await transcripts.run_passes(
            record, [transcripts.gate_pass(), transcripts.polish_pass(llm),
                     transcripts.structure_pass()])
        gate = next((p for p in record["passes"] if p["name"] == "gate"), {})
        if str(gate.get("outcome", "")).startswith("blocked"):
            _err(f"  transcript of {message.event_id} unusable "
                 f"({gate['outcome']}); keeping the recording without words")
        return record

    try:
        return (await voice.TRANSCRIPTS.run(
            message.event_id, produce, force=retranscribe))["text"]
    except LLMError as e:
        _err(f"  could not transcribe {message.event_id}: {e}")
        return ""


# How many messages go to the model at once, and how many of the
# previous chunk to repeat. The overlap exists so a recording split
# across a chunk boundary is still seen whole by one call; a split is
# always two adjacent uploads, so a few messages of run-up is plenty.
_CHUNK = 40
_OVERLAP = 4

# One small JSON object per message in the slice, plus the enclosing
# structure. A model that loops instead of closing the array is capped
# here rather than at the client timeout.
# The budget covers the facts plus the distillation: a gist sentence
# and up to three copied passages for long messages.
_READ_TOKENS_PER_MESSAGE = 240
_READ_TIMEOUT_S = 300.0

# Two to four sentences.
_SUMMARY_TOKENS = 600
_SUMMARY_TIMEOUT_S = 180.0


_READ_PROMPT = """\
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

"continues": the number of the message directly before this one, when
  the two are halves of a single recording that was cut in the middle
  of a sentence: the earlier one stops mid-thought and this one picks
  up the same sentence. Otherwise null. Messages that merely arrived
  together are not halves of each other -- three uploads in the same
  second are usually three separate memos, and joining them would fuse
  three memories into one.

"refers_to": the number of an earlier message this one is a remark
  about rather than a memory of its own: a caption for a photo, or a
  line like "the picture above is from the barbecue". A message that
  reports something that happened is a memory of its own even when it
  follows up on an earlier one -- "his cast came off today" answers a
  recording from weeks ago and is still its own memory, not a footnote
  to it. Use this only when the message would make no sense on its own
  page. Otherwise null.

"gist": for a message longer than about 100 words: one sentence in
  {language}, saying what it is about and for whom. Plain and
  specific, no marketing words. For shorter messages null.

"moments": for a message longer than about 100 words: up to three
  short passages copied word-for-word from the message, the lines most
  worth keeping. Copy them exactly as written, complete sentences
  only, no edits. Otherwise an empty list.
"""


def _as_prompt(chunk) -> str:
    """The messages as the reader sees them, numbered from one.

    Numbered rather than keyed by event id because the links come back
    as references and a model copying a 43-character Matrix id is a
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
        start += _CHUNK - _OVERLAP
    return out


async def _read_room(messages, llm, cache=None):
    """Read the room: facts per message, and the links between them.

    One call per slice of history rather than one per message. Reading a
    message alone cannot see that it finishes the sentence before it, or
    that it is a remark about the photo above -- and a call with no
    neighbours also mistakes a two-person conversation for one person
    reminiscing. The batch is what makes those answerable.

    What the model finds is remembered against the message it describes.
    Both halves of that are permanent: a message never changes (an edit
    is a new event pointing at the old one), and a link, once seen, is a
    fact about two messages rather than about the run they arrived in.
    So a slice read end to end is never sent again, and a nightly pass
    costs only what is genuinely new.

    Returns `(readings, continues, refers_to)`. A slice the model fails
    or garbles contributes nothing and the rest still compiles: the
    entries are the family's words either way, and a missing reading
    costs a date, not a memory.
    """
    readings: dict[str, diary.Reading] = {}
    continues: dict[str, str] = {}
    refers_to: dict[str, str] = {}
    known: set[str] = set()

    def remember(event_id: str, row: dict) -> None:
        readings[event_id] = diary.Reading(
            mode=str(row.get("mode") or "monologue"),
            spoken_date=row.get("spoken_date") or None,
            addressee=row.get("addressee") or None,
            gist=row.get("gist") or None,
            moments=tuple(row.get("moments") or ()),
        )
        if target := row.get("continues"):
            continues[event_id] = target
        if target := row.get("refers_to"):
            refers_to[event_id] = target
        known.add(event_id)

    if cache is not None:
        for msg in messages:
            stored = cache.get(msg.event_id, msg.body)
            # Readings from before distillation carry no "moments" key.
            # Treat them as absent so the message is read again once.
            if stored is not None and "moments" in stored:
                remember(msg.event_id, stored)
    if known:
        _err(f"  {len(known)} message(s) already read, "
             f"{len(messages) - len(known)} new")

    slices = _chunks(messages)
    for n, chunk in enumerate(slices, start=1):
        # A slice whose every message is on file has nothing left to
        # say: its links were recorded with the messages they join.
        if all(m.event_id in known for m in chunk):
            continue
        _err(f"  reading slice {n} of {len(slices)}")

        prompt = _READ_PROMPT.format(
            language=_household_language(), messages=_as_prompt(chunk))
        try:
            raw = await llm.complete(
                "classifier", prompt, json_mode=True, temperature=0,
                max_tokens=len(chunk) * _READ_TOKENS_PER_MESSAGE + 256,
                timeout=_READ_TIMEOUT_S)
            payload = json.loads(raw)
            rows = payload.get("messages") if isinstance(payload, dict) else None
        except (LLMError, json.JSONDecodeError, TypeError) as e:
            _err(f"  could not read {len(chunk)} message(s): {e}")
            continue
        if not isinstance(rows, list):
            _err(f"  unreadable answer for {len(chunk)} message(s)")
            continue

        for row in rows:
            if not isinstance(row, dict):
                continue
            here = _resolve_n(row.get("n"), chunk)
            if here is None or here.event_id in known:
                continue
            found = {
                "mode": str(row.get("mode") or "monologue"),
                "spoken_date": row.get("spoken_date") or None,
                "addressee": row.get("addressee") or None,
                "continues": _link(row.get("continues"), chunk),
                "refers_to": _link(row.get("refers_to"), chunk),
                "gist": (str(row.get("gist")).strip()
                         if row.get("gist") else None),
                "moments": [str(m) for m in row.get("moments") or []
                            if str(m).strip()][:5],
            }
            remember(here.event_id, found)
            if cache is not None:
                cache.put(here.event_id, found, here.body)

        # Saved per slice rather than once at the end, so an
        # interrupted run resumes from the last completed slice.
        if cache is not None:
            cache.save()

    return readings, continues, refers_to


def _link(value, chunk) -> str | None:
    """The event id a returned number points at, or None."""
    target = _resolve_n(value, chunk)
    return target.event_id if target is not None else None


def _resolve_n(value, chunk):
    """The message a returned number points at, or None if it points off
    the end. Models occasionally answer with a number that is not in
    front of them; a link into thin air is dropped rather than guessed."""
    if not isinstance(value, int) or not 1 <= value <= len(chunk):
        return None
    return chunk[value - 1]


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
- Write in {language}. The diary belongs to a family that speaks it.
- Use only what the entries say. Never add an event, a feeling, a place
  or an outcome that is not in them.
- Keep every detail with the person the entry keeps it with. Do not move
  something one child did onto another child.
- Keep the direction of what happened. If one person did something for,
  to, or about another, do not swap them round.
- Prefer reported speech: write what people recorded, told and
  described ("Marge erzählt, dass ..."), not bare statements of fact.
  These entries are people telling things, and the diary recalls the
  telling.
- An entry marked as a conversation has no speaker labels in its text.
  Name its participants and its topics. Never attribute a statement
  inside it to a named person.
- A word listed as "unclear" was not heard clearly. Do not use it, and
  do not attribute anything to a person through it.
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


# The prompt is English, so without a stated target language the model
# answers in English. The household language comes from the core env.
_LANGUAGE_NAMES = {"de": "German", "en": "English"}


def _household_language() -> str:
    code = (os.environ.get("LANGUAGE") or "").strip().lower()[:2]
    return _LANGUAGE_NAMES.get(
        code, "the language the entries are written in")


def _unclear_words(entry) -> list[str]:
    """Words whisper did not hear clearly, for this entry's recordings.

    Read from the transcript records' quality data. The summariser is
    told these words are unreliable, so it cannot hang an event or a
    person on a misheard name.
    """
    words: list[str] = []
    for eid in entry.event_ids:
        record = voice.TRANSCRIPTS.read(eid) or {}
        for w in (record.get("quality") or {}).get("low_words") or []:
            token = (w.get("word") or "").strip()
            if token and token not in words:
                words.append(token)
    return words[:5]


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
        # The mode separates safe attribution from unsafe: a monologue
        # has one speaker (the sender); a conversation carries no
        # speaker labels inside its text.
        if entry.kind == "voice" and entry.mode == "dialogue":
            role = (f"{who} recorded a conversation "
                    f"(who said which line is unknown)")
        elif entry.kind == "voice":
            role = f"{who} spoke"
        else:
            role = who
        body = entry.body.strip() or "(a photo, no caption)"
        for sender, text in entry.comments:
            body += f"\n  {sender.title()} replied: {text.strip()}"
        line = f"- [{when}] {role}: {body}"
        if unclear := _unclear_words(entry):
            line += ("\n  unclear words, not heard clearly: "
                     + ", ".join(unclear))
        out.append(line)
    return "\n".join(out)


async def _summarise(entries, llm) -> str:
    """A paragraph recalling one month, or "" if the model cannot.

    Temperature 0, so a recompile of an unchanged month reads the same
    way. A failure returns empty and the page falls back to its factual
    opening -- a diary missing its introduction is fine, a diary whose
    introduction changes wording every night is not.
    """
    month = entries[0].on.strftime("%B %Y")
    prompt = _SUMMARY_PROMPT.format(language=_household_language(), month=month, evidence=_evidence(entries))
    try:
        text = await llm.complete("writer", prompt, temperature=0,
                                  max_tokens=_SUMMARY_TOKENS,
                                  timeout=_SUMMARY_TIMEOUT_S)
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


def _household_zone():
    """The clock the family keeps, for turning timestamps into days.

    Falls back to UTC with a warning rather than failing: a diary with
    some entries an hour either side of midnight is worth more than no
    diary, and the operator can see why in the output.
    """
    name = os.environ.get("TIMEZONE", "").strip()
    if not name:
        _err("TIMEZONE not set, reading timestamps as UTC "
             "— late-night entries may land on the wrong day")
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        _err(f"unknown timezone {name!r}, reading timestamps as UTC")
        return timezone.utc


# Flags that consume the token after them, so the room can be picked out
# of the rest without mistaking a flag's value for it.
_TAKES_A_VALUE = ("--burst-window", "--limit")


def _opt(argv: list[str], flag: str, fallback: str) -> str:
    for i, arg in enumerate(argv):
        if arg == flag and i + 1 < len(argv):
            return argv[i + 1]
    return fallback


def _positional(argv: list[str], fallback: str) -> str:
    """The room, named the way every other command names one.

    `stack messages read <room>`, `join <room>`, `send <room>`: a room
    is a positional everywhere in this CLI, so it is one here too.
    """
    skip = False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg in _TAKES_A_VALUE:
            skip = True
            continue
        if not arg.startswith("-"):
            return arg
    return fallback


async def run(llm, argv: list[str]) -> int:
    room_arg = _positional(argv, "memories")
    dry_run = "--dry-run" in argv
    rebuild = "--force" in argv
    # --force re-reads and re-summarises but keeps transcripts: whisper
    # costs minutes of GPU per recording and its output only changes
    # when its config or vocabulary does. --retranscribe is that case.
    retranscribe = "--retranscribe" in argv
    try:
        window = float(_opt(argv, "--burst-window",
                            str(diary.DEFAULT_BURST_WINDOW_S)))
    except ValueError:
        _err("--burst-window wants a number of seconds")
        return 2
    try:
        limit = int(_opt(argv, "--limit", "0"))
    except ValueError:
        _err("--limit wants a number of messages")
        return 2

    zone = _household_zone()
    # Pages render in the household language. Selected once per run.
    diary.configure_language(os.environ.get("LANGUAGE", ""))
    readings_cache, summaries_cache = diary_store.open_stores()
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

    # Recordings can be tens of megabytes; the default five seconds is
    # for APIs, not for media.
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as session:
        try:
            token = await _admin_token(session, homeserver)
            room_id = await _resolve_room(session, homeserver, token, room_arg)
            events = await _history(session, homeserver, token, room_id,
                                    limit=limit)
        except (RuntimeError, httpx.HTTPError) as e:
            _err(str(e))
            return 1

        messages = diary.resolve(events, burst_window_s=window, zone=zone)
        if not messages:
            _err(f"nothing in {room_arg} to compile")
            return 0
        _err(f"{len(messages)} message(s) in {room_arg}")

        # Transcription first and on its own: every later step reads
        # words, and a recording that cannot be decoded should drop out
        # before the model is asked to interpret its filename.
        vocabulary = _household_vocabulary()
        if vocabulary:
            _err(f"  decoding against: {vocabulary[:90]}...")

        # Transcription dominates runtime, so each recording is logged
        # before the whisper call rather than after. Cached transcripts
        # return immediately and are not logged.
        recordings = [m for m in messages if m.kind == "voice"]
        pending = sum(1 for m in recordings
                      if voice.TRANSCRIPTS.read(m.event_id) is None)
        if recordings:
            _err(f"  {len(recordings)} recording(s), {pending} to decode")

        decoded, heard = [], 0
        for msg in messages:
            if msg.kind != "voice":
                decoded.append(msg)
                continue
            heard += 1
            if voice.TRANSCRIPTS.read(msg.event_id) is None:
                _err(f"  [{heard}/{len(recordings)}] decoding {msg.sender}, "
                     f"{_length(msg.duration_ms)}")
            text = await _transcribe(
                msg, session=session, homeserver=homeserver, token=token,
                transcriber=transcriber, llm=llm, vocabulary=vocabulary,
                retranscribe=retranscribe)
            if not text.strip():
                record = voice.TRANSCRIPTS.read(msg.event_id) or {}
                if (record.get("raw") or "").strip():
                    # Whisper heard something but the gate judged it
                    # unusable (loop, CJK, failed segments). The memory
                    # is not dropped: the entry keeps its place and its
                    # audio, with no words attached.
                    decoded.append(msg.__class__(**{**msg.__dict__, "body": ""}))
                else:
                    _err(f"  no speech in {msg.event_id}, skipped")
                continue
            decoded.append(msg.__class__(**{**msg.__dict__, "body": text}))

        readings, continues, refers_to = await _read_room(
            decoded, llm, None if rebuild else readings_cache)

    await transcriber.aclose()

    entries = diary.compile_entries(decoded, readings,
                                    continues=continues,
                                    refers_to=refers_to)
    _err(f"{len(entries)} diary entr{'y' if len(entries) == 1 else 'ies'}")

    months: dict[str, list] = {}
    for entry in entries:
        key = f"{diary.year_key(entry.on)}-{diary.month_key(entry.on)}"
        months.setdefault(key, []).append(entry)

    summaries = {}
    for key, in_month in sorted(months.items()):
        digest = diary.month_digest(in_month)
        kept = "" if rebuild else summaries_cache.get(key, digest)
        if kept:
            summaries[key] = kept
            continue
        _err(f"  summarising {in_month[0].on.strftime('%B %Y')}")
        summaries[key] = await _summarise(in_month, llm)
        if summaries[key]:
            summaries_cache.put(key, digest, summaries[key])
            summaries_cache.save()

    readings_cache.save()
    summaries_cache.save()

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
