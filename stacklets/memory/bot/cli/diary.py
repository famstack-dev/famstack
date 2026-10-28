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
    stack memory diary --rebuild            write every card again under the
                                            current prompts (a compile
                                            otherwise writes a card again
                                            only when its entry changed)
    stack memory diary --retranscribe       decode all recordings again
                                            (after a whisper config or
                                            vocabulary change)
    stack memory diary --preview 3          the 3 newest entries' cards, as
                                            they are on file and as
                                            --rebuild makes them now, as
                                            JSON (the host renders it);
                                            writes nothing but the reading
                                            caches
    stack memory diary --limit 20           only the newest 20 messages,
                                            always a dry run: writes no
                                            card, removes none, publishes
                                            no page
    diary pages [--fast] [--month 2026-10]  the pages again from the records
                                            on file, no room read (the
                                            curator, after a push)

WHY THE BURST WINDOW IS A KNOB
    Messages that synced late carry arrival timestamps, not recording
    times, and the tell is that they land in a tight run. In a real room
    the gaps between live messages are hours, so 120s separates the two
    cleanly. A *replayed* test corpus compresses those gaps to seconds
    and needs a window well under a second. There is no single value
    that fits both, so the caller picks.

A dry run publishes no pages but still writes the transcript, reading
and summary caches, and still keeps the originals it reads. None of
those are output: discarding them would make a preview run as expensive
as the compile that follows, and a preview whose pages embed nothing
would not be a preview of what publishing produces.

Runs inside `stack-core-bot-runner`: it has the whisper client, the LLM
client, the brain working copy, and the Matrix admin credentials. The
host-side wrapper is a thin docker-exec, like `stack memory wiki`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# httpx rather than aiohttp: the OpenAI SDK already pulls it into every
# container that can talk to a model, so the compiler runs unchanged in
# the bot-runner and in the curator that schedules it nightly.
import httpx

from .. import brain_lock
from .. import call_costs
from .. import diary
from .. import diary_card
from .. import diary_store
from stack.ai.client import LLMError, Transcriber
from stack.ai.language import LANGUAGES, language_code
from stack.ai.transcripts import TRANSCRIPTS
from stack.frontmatter import FrontmatterError, parse as parse_frontmatter
from stack.ai import transcripts
from stack.briefing import is_short_text
from stack.cards import card_version
from stack import media
from stack.people import Person as DeclaredPerson, find_people
from stack.tags import tag_label

from ...lib import (
    Person,
    get_ontology,
    load_persons_from_vault,
    persons_prompt_section,
)
from ...memory_api import MEMORY_BOT, CuratorDown, MemoryAPI, Refused, put, remove

from . import wiki

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


# ── Keeping the originals ─────────────────────────────────────────────
#
# The room is the only copy of everything a family posts into it. The
# pages compiled below describe those recordings and photographs and
# link back to them, which is worth nothing on the day the homeserver's
# media store is not there any more. So the compile keeps the file too,
# in the archive the wiki serves, and the page embeds what it kept.

# What `stack.media` calls each kind of message the compiler
# recognises. `text` is absent on purpose: it carries no file.
_ARCHIVE_KIND = {"voice": "audio", "image": "image",
                 "video": "video", "file": "file"}


async def _archive_media(messages, events, *, session, homeserver, token,
                         room_id: str) -> "dict[str, str]":
    """Keep the file behind every upload; return where each one landed.

    Keyed by event id, in the site-rooted form a page embeds, which is
    what `diary.pages_for` takes. The value is the copy meant to be
    shown: the AAC version of a recording, the bounded version of a
    photograph, the original for everything else.

    Nothing here is allowed to stop a compile. An unreachable
    homeserver, a full disk, a container without ffmpeg: each costs an
    entry its file and nothing more. The entry is left out of the map
    and its page renders the way it did before any of this existed.

    Bytes already in the archive are not fetched again. A compile walks
    the room's whole history every night, and re-downloading years of
    photographs to write files that are already on disk would make
    every night cost what the first one did.
    """
    root = media.open_archive()
    if root is None:
        _err("  no media archive configured, keeping no originals")
        return {}

    raw = {ev.get("event_id"): ev for ev in events}
    paths: "dict[str, str]" = {}
    fetched = 0
    for msg in messages:
        kind = _ARCHIVE_KIND.get(msg.kind, "")
        if not kind or not msg.url:
            continue
        event = raw.get(msg.event_id) or {}
        content = event.get("content") or {}
        filename = content.get("filename") or content.get("body") or ""
        mime = (content.get("info") or {}).get("mimetype") or ""
        ext = media.extension_for(filename, mime)
        # Filed under when it arrived rather than under the date its
        # entry ends up carrying: the archive indexes events, and the
        # record beside each file states that same moment. The page
        # addresses the file by path, so the two need not agree.
        when = datetime.fromtimestamp(msg.ts / 1000, timezone.utc)

        link = media.kept(root, msg.event_id, ext=ext, when=when)
        if not link:
            # Logged before the fetch, not after. A first compile over a
            # room with years of photographs in it is minutes of
            # downloading, and a command that prints nothing for that
            # long is indistinguishable from one that has hung.
            _err(f"  keeping {filename or msg.event_id}")
            data = await _download(session, homeserver, token, msg.url)
            if not data:
                continue
            link = media.keep(
                root, msg.event_id, data, ext=ext, when=when, kind=kind,
                source="diary", mime=mime, filename=filename,
                room_id=room_id, sender=event.get("sender") or "",
            )
            if not link:
                continue
            fetched += 1
        paths[msg.event_id] = media.derive(
            root, msg.event_id, ext=ext, when=when, kind=kind) or link

    if fetched:
        _err(f"  kept {fetched} original(s)")
    return paths


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


def _declared() -> list[DeclaredPerson] | None:
    """The family's person files; None until `stack up memory` wrote them.

    A file that does not read is logged with its path and left out.
    """
    return find_people(
        (os.environ.get("MEMORY_VAULT_DIR"), os.environ.get("BRAIN_REPO_DIR")),
        os.environ.get("SHARED_BUCKET") or "family",
        report=lambda problem: _err(f"person file skipped: {problem}"),
    )


def _household_people() -> list[str]:
    """The household's names: the person files, then the wiki's person pages.

    The person files declare everyone the family knows, a baby without
    an account too, with every name the family uses for them. Until they
    exist the accounts stand in (FAMILY_NAMES, from users.toml). The
    pages add the spellings documents used. Best-effort: no brain means
    the declared names alone.
    """
    brain = Path(os.environ.get("BRAIN_REPO_DIR", ""))
    abouts = sorted(brain.glob("*/about.md")) if brain.is_dir() else []
    declared = _declared()
    if declared is None:
        return diary.household(os.environ.get("FAMILY_NAMES", ""), [_frontmatter(a) for a in abouts])
    # A person's page is the folder named by their id (`<id>/about.md`).
    ids = {p.id for p in declared}
    names = ",".join(n for p in declared for n in p.all_names())
    return diary.household(names, [_frontmatter(a) for a in abouts if a.parent.name in ids])


def _declared_persons(declared: list[DeclaredPerson], learned: list[Person]) -> list[Person]:
    """The person files as the classifier's persons: first name canonical.

    The first name is the name cards carry and the `Person:` tag the
    archivist files under, so a card about "Margaret" lists Maggie the
    same way a document about her is tagged. The full name, the
    family's aliases and the spellings documents used are the synonyms.
    """
    by_slug = {p.slug: p for p in learned}
    persons = []
    for d in declared:
        names = [n for n in d.all_names() if n != d.first_name]
        seen = by_slug.get(d.id)
        for n in seen.all_known_names() if seen else []:
            if n.lower() not in {x.lower() for x in [d.first_name, *names]}:
                names.append(n)
        persons.append(Person(canonical=d.first_name, slug=d.id, synonyms=names))
    return persons


def _household_vocabulary() -> str:
    """The names and subjects this family uses, for whisper to decode against.

    People come from users.toml and the wiki's person pages. The ontology's topics
    follow, because a family's proper nouns are not only its people --
    a campsite, a school, a pet -- and those mishear just as readily.

    Best-effort: a vault that has not been generated yet simply yields
    nothing, and transcription proceeds exactly as it did before.
    """
    people = _household_people()

    topics: list[str] = []
    vault = Path(os.environ.get("MEMORY_VAULT_DIR", ""))
    lang = os.environ.get("LANGUAGE", "en")
    try:
        from stack.ontology import Ontology
        loaded = Ontology.load(vault / "ontology.toml")
        for topic in loaded.topics.values():
            topics.append(topic.name(lang))
            topics.extend(topic.synonyms_for(lang))
    except Exception as e:
        _err(f"  no ontology for the transcript hint: {e}")

    hint = diary.spoken_vocabulary(people, topics)
    if len(hint) <= _VOCAB_BUDGET:
        return hint
    # Over budget: keep the people, who are what mishears most.
    return diary.spoken_vocabulary(people)[:_VOCAB_BUDGET]


def _frontmatter(path: Path) -> dict:
    """A page's frontmatter, or {} when it has none that parses (and says so)."""
    try:
        return parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    except FrontmatterError as e:
        _err(f"{path}: frontmatter does not parse ({e}), skipped")
        return {}


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
        record = await transcripts.run_passes(record, chain)
        gate = next((p for p in record["passes"] if p["name"] == "gate"), {})
        if str(gate.get("outcome", "")).startswith("blocked"):
            _err(f"  transcript of {message.event_id} unusable "
                 f"({gate['outcome']}); keeping the recording without words")
        return record

    try:
        record = await TRANSCRIPTS.run(
            message.event_id, produce, force=retranscribe)
        # A cached record skipped produce() and with it the passes. A
        # changed pass fingerprint (prompt edit, new household member,
        # version bump) re-runs the whole chain from raw text. Whisper
        # does not run again.
        if stale := transcripts.stale_passes(record, chain):
            _err(f"  passes changed for {message.event_id}: "
                 + ", ".join(p.name for p in stale))
            record = dict(record)
            record["text"] = record.get("raw") or ""
            record = await transcripts.run_passes(record, chain)
            TRANSCRIPTS.write(message.event_id, record)
        return record["text"]
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
_READ_TOKENS_PER_MESSAGE = 120
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
        )
        if target := row.get("continues"):
            continues[event_id] = target
        if target := row.get("refers_to"):
            refers_to[event_id] = target
        known.add(event_id)

    if cache is not None:
        for msg in messages:
            if (stored := cache.get(msg.event_id, msg.body)) is not None:
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

        prompt = _READ_PROMPT.format(messages=_as_prompt(chunk))
        try:
            raw = await llm.complete(
                "classifier", prompt, json_mode=True, temperature=0,
                max_tokens=len(chunk) * _READ_TOKENS_PER_MESSAGE + 256,
                timeout=_READ_TIMEOUT_S, on_cost=call_costs.recorder("diary-room"))
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

The people in this family are: {people}. No other family members
exist. A name in the entries that is not in this list is a mishearing:
leave it out.

Write two to four short sentences recalling that month, the way
someone in the family would remember it later. One moment per
sentence. Do not chain several events into one sentence with words
like "before", "while" or "and then". The summary is not a complete
account: pick at most four moments and let the rest go -- the entries
below the paragraph carry everything else.

Rules:
- Write in {language}. The diary belongs to a family that speaks it.
- Use only what the entries say. Never add an event, a feeling, a place
  or an outcome that is not in them.
- Keep every detail with the person the entry keeps it with. Do not move
  something one child did onto another child.
- Keep the direction of what happened. If one person did something for,
  to, or about another, do not swap them round.
- Write a warm chronicle in the third person: what the month had to
  tell. Prefer reported speech -- what people recorded, told and
  described -- over bare statements of fact. Use verbs, not
  nominalizations. Never a list of topics: pick the moments that
  matter and let the rest go.

  Wanted register (an invented example, not this family):
    "September had a lot to tell: Anna recorded the first tooth
    finally coming through, and around the kitchen table the summer
    at the lake came back to life -- including the shoe that stayed
    in the water."
  Not wanted: "In September Anna and Jonas reported on a holiday, a
  first tooth and a concert."
- An entry marked as a conversation has no speaker labels in its text.
  Name its participants and its topics. Never attribute a statement
  inside it to a named person.
- Never put a role in place of a name. When an entry does not say who
  else was there, name the people it could be, joined with "or", or
  leave them out.
- [unclear] marks a word that was not heard clearly. Never guess what
  it was, and never treat it as a person.
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
def _household_language() -> str:
    return LANGUAGES.get(language_code(os.environ.get("LANGUAGE") or ""),
                         "the language the entries are written in")


def _unclear_words(entry) -> list[str]:
    """Words whisper did not hear clearly, for this entry's recordings.

    Read from the transcript records' quality data. The summariser is
    told these words are unreliable, so it cannot hang an event or a
    person on a misheard name.
    """
    words: list[str] = []
    for eid in entry.event_ids:
        record = TRANSCRIPTS.read(eid) or {}
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
        # Unclear words are removed from what the summariser sees.
        # Telling the model a word is unreliable does not stop it from
        # using it; a word it never sees cannot become a person.
        for token in _unclear_words(entry):
            body = re.sub(rf"\\b{re.escape(token)}\\b", "[unclear]", body)
        out.append(f"- [{when}] {role}: {body}")
    return "\n".join(out)


def _month_page(in_month, *, bucket: str) -> str:
    """The path of the page a month's paragraph opens, as the brain holds it:
    the month's page among the diary's pages (`diary.pages_for`), in `bucket`."""
    [month] = [page for page in diary.pages_for(in_month, bucket=bucket)
               if page.view == "diary-month"]
    return f"{bucket}/{month.path}"


async def _summarise(entries, llm, *, bucket: str) -> str:
    """A paragraph recalling one month, or "" if the model cannot.

    Temperature 0, so a recompile of an unchanged month reads the same
    way. A failure returns empty and the page falls back to its factual
    opening -- a diary missing its introduction is fine, a diary whose
    introduction changes wording every night is not.
    """
    month = entries[0].on.strftime("%B %Y")
    prompt = _SUMMARY_PROMPT.format(
        language=_household_language(), month=month,
        people=", ".join(_household_people()) or "unknown",
        evidence=_evidence(entries))
    try:
        text = await llm.complete("writer", prompt, temperature=0,
                                  max_tokens=_SUMMARY_TOKENS,
                                  timeout=_SUMMARY_TIMEOUT_S,
                                  on_cost=call_costs.recorder(
                                      "diary-month", _month_page(entries, bucket=bucket)))
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


# ── Reading an entry for its card ─────────────────────────────────────
#
# One call per entry, after the room has been read and the entries
# compiled: the entry is the unit a card describes, so it is the unit the
# model reads. A recording joined from two uploads is read once, whole,
# with its replies. What comes back is held to the household's vocabulary
# (`diary_card.extraction_from`) before it touches a card.


_EXTRACT_TOKENS = 900
_EXTRACT_TIMEOUT_S = 180.0

_EXTRACT_PROMPT = """\
You are filing one entry from a family's private memories room into the
family archive. Report what the entry says. Never translate its words,
never add to them. When you quote, copy the words exactly.

{vocabulary}

{people}

The entry:
{entry}

The people in it: name them, in the title, the description, the summary
and "persons", and never describe someone by their place in the family
instead. The entry says who recorded it. When the words name someone
else who took part, or address them by name, name that person. When
they show that someone else took part but not who, that person is not
known, and naming one of them as if it were is wrong. Name the people it
could be instead: every family member from the list the words do not
rule out (by how they speak to the one recording, what they do, how old
they must be), joined with "or". Never pick one because they seem more
likely. A family member corrects the card when it is wrong.

Reply with a JSON object with these keys:

"title": a short name for the entry, three to six words, in {language}.
  Name what happened, not the medium: "Bart's first bike ride", not
  "Voice note".

"description": one sentence in {language} saying what this entry is.

{summary}

{facts}

"persons": the family members the entry is about, spoken to or who took
  part, using the names from the family list above; when the words do
  not say who took part, the people it could be. Only names from that
  list.

"topics": up to three topics from the vocabulary above that fit the
  entry. Only topics from that vocabulary. An empty list when none fits.

"quotes": when the entry's words are longer than about 100 words: up to
  three short passages copied word for word from them, complete
  sentences only, the lines most worth keeping. Otherwise an empty list.
"""


_SUMMARY_FIELD = """\
"summary": one to three short sentences in {language} saying what the
  entry is about, naming its people as above. Write with verbs, as
  things that happened, never as a list of topics. In a recorded
  conversation, say who took part and what it was about, never who said
  which line. No marketing words.
  Wanted register (an invented example, not this family):
    "Anna and Jonas or Mia talk about the day at the lake and the shoe
    that landed in the water."
  Not wanted: "Anna and Jonas report on an outing, a picnic, a lost
  shoe and the weather.\""""

_FACTS_FIELD = """\
"facts": what someone would want to look up about this entry later, in
  {language}, each as "Label: value". The label is who or what the fact
  is about: a family member's name ("Maggie: first tooth"), a place
  ("Place: Springfield Lake"), a thing ("Swimming badge: 25 metres"),
  never the kind of fact ("Action", "Event", "Detail", "Date", "Person").
  Milestones first: a first step, a first word, a badge, a birthday, a
  new school. Leave out what the summary already says in a sentence and
  the day the entry happened. At most five. An empty list when the entry
  holds nothing worth looking up, which is often."""

# A short entry's card shows its words, and a summary of them would be as
# long as they are (`stack.briefing.is_short_text`). `diary_card` drops
# one the model writes anyway.
_SHORT_SUMMARY_FIELD = """\
"summary": an empty string. The entry is short and its card shows its
  words as they are, so a summary would only repeat them."""

_SHORT_FACTS_FIELD = _FACTS_FIELD + """
  The card shows the entry's words, so a fact is only worth it for what
  someone would look up on its own: a milestone, a date, an amount, a
  name. Never a line that says part of the entry again."""


def _reading_fields(entry, language: str) -> dict[str, str]:
    """The prompt's summary and facts keys, for a short or a long entry."""
    short = is_short_text(entry.body)
    return {
        "summary": (_SHORT_SUMMARY_FIELD if short else _SUMMARY_FIELD).format(language=language),
        "facts": (_SHORT_FACTS_FIELD if short else _FACTS_FIELD).format(language=language),
    }


def _entry_prompt(entry) -> str:
    """One entry as the model reads it: who, when, what, and the words."""
    when = ("date unknown" if entry.confidence == "uncertain"
            else entry.on.isoformat())
    kind = {"voice": "voice recording", "image": "photo", "video": "video",
            "file": "file"}.get(entry.kind, "written note")
    if entry.kind == "voice" and entry.mode == "dialogue":
        kind = "recorded conversation (no speaker labels)"
    lines = [f"Recorded by {entry.sender.title()}, {when}, {kind}."]
    if entry.addressee:
        lines.append(f"Spoken to: {entry.addressee}.")
    lines.append("")
    lines.append(entry.body.strip() or "(no words)")
    for who, text in entry.comments:
        lines += ["", f"{who.title()} replied: {text.strip()}"]
    return "\n".join(lines)


def _extract_prompt(entry, *, vocabulary: str, people_section: str) -> str:
    """The prompt that reads one entry for its card."""
    language = _household_language()
    return _EXTRACT_PROMPT.format(
        vocabulary=vocabulary, people=people_section or "",
        entry=_entry_prompt(entry), language=language,
        **_reading_fields(entry, language))


def _unsure_words(event_ids) -> set[str]:
    """Words whisper flagged as unsure in any of these recordings."""
    unsure: set[str] = set()
    for event_id in event_ids:
        record = TRANSCRIPTS.read(event_id) or {}
        for w in (record.get("quality") or {}).get("low_words") or []:
            if word := (w.get("word") or "").strip().lower():
                unsure.add(word)
    return unsure


def _sure_quotes(quotes, event_ids) -> list[str]:
    """Drop quotes holding a word whisper was unsure of.

    A quote is the most prominent text on a diary page; it must not
    showcase a word the recognizer flagged.
    """
    unsure = _unsure_words(event_ids)
    if not unsure:
        return list(quotes)
    return [q for q in quotes
            if not set(re.findall(r"[\wÀ-ɏ]+", q.lower())) & unsure]


async def _extract_all(entries, llm, cache, *, ontology, people: dict,
                       declared: list[DeclaredPerson] | None,
                       vocabulary: str, people_section: str,
                       language_code: str, current: bool = False,
                       force: bool = False) -> list:
    """What a model reads out of each entry, cached per entry.

    The cache is keyed by the entry's sources (`_sources`), not by the
    prompt or the vocabulary it offers, so a reading on file for the
    entry as it is now is used whatever prompts made it. `current` takes
    only a reading made under the current prompt template (`_template`);
    `force` takes none. An entry the model fails on still gets an empty
    extraction: its card is written with a plain title and the family's
    words, and a later run fills in the rest.
    """
    model = os.environ.get("AI_DEFAULT_MODEL", "")

    def on_file(entry) -> "dict | None":
        if force:
            return None
        return cache.get(diary_card.entry_id_for(entry.event_ids[0]), _sources(entry),
                         _template(entry) if current else None)

    out = []
    todo = sum(1 for e in entries if on_file(e) is None)
    if todo:
        _err(f"  reading {todo} entr{'y' if todo == 1 else 'ies'} for their cards")
    for entry in entries:
        entry_id = diary_card.entry_id_for(entry.event_ids[0])
        raw = on_file(entry)
        template = str((raw or {}).get("template") or "")
        if raw is None:
            template = _template(entry)
            try:
                answer = await llm.complete(
                    "classifier", _extract_prompt(entry, vocabulary=vocabulary,
                                                  people_section=people_section),
                    json_mode=True, temperature=0,
                    max_tokens=_EXTRACT_TOKENS, timeout=_EXTRACT_TIMEOUT_S,
                    on_cost=call_costs.recorder("diary-entry"))
                raw = json.loads(answer)
            except (LLMError, json.JSONDecodeError, TypeError) as e:
                _err(f"  could not read the entry {entry_id}: {e}")
                raw = None
            if isinstance(raw, dict):
                cache.put(entry_id, raw, _sources(entry), template)
                cache.save()
            else:
                template = ""
        extraction = diary_card.extraction_from(
            raw, ontology=ontology, language=language_code, people=people,
            declared=declared, model=model)
        _report_unknown(entry_id, extraction)
        out.append(replace(extraction, template=template,
                           quotes=_sure_quotes(extraction.quotes, entry.event_ids)))
    return out


def _sources(entry) -> str:
    """What an entry is made of: its messages, its words and its replies.

    The key a reading is kept under. The date, the addressee and the
    kind of recording are left out: they come from the model's reading
    of the room, and a new reading of the room is not a new entry.
    """
    return json.dumps([entry.event_ids, " ".join(entry.body.split()),
                       [[who, " ".join(text.split())] for who, text in entry.comments]],
                      ensure_ascii=False)


# What stands in for each value a card prompt is built from when its
# skeleton is hashed (`_template`).
_VALUE = "<value>"


def _template(entry) -> str:
    """A short hash of the card prompt's skeleton for this entry.

    The prompt with every value that varies (the entry, the vocabulary,
    the family, the language) replaced by a placeholder, as notes name
    theirs (`capture_prompt_template` in the archivist). A change to the
    prompt's wording changes it; a new topic or a new person does not.
    A short entry and a long one take different branches of the prompt
    (`_reading_fields`), so each has its own.
    """
    fields = _reading_fields(entry, _VALUE)
    return transcripts.fingerprint(_EXTRACT_PROMPT.format(
        vocabulary=_VALUE, people=_VALUE, entry=_VALUE, language=_VALUE, **fields))


# ── The vault ─────────────────────────────────────────────────────────
#
# Cards are records, so they go to the memory, the repository documents
# and captures are filed into, through the curator, its one writer. The
# records are read from the curator with the version each is at, and every
# write names the version it was made from: a card a person corrected in
# Forgejo's web editor in between fails the commit instead of being lost.


# Files per commit. A first compile over years of a room writes hundreds
# of cards; batches keep each request a modest size.
_COMMIT_BATCH = 100

# Where every card of a compile or a correction comes from, as its commit says.
_SOURCE = {"Source": "memories room"}


def _read_diary_tree(memory: MemoryAPI, bucket: str) -> "dict[str, tuple[str, str]]":
    """Every diary record in the memory: path -> (version, content)."""
    root = f"{bucket}/{diary.DIARY_DIR}/{diary_card.ENTRIES_DIR}"
    return {f.path: (f.version, f.text) for f in memory.files(root) if f.path.endswith(".md")}


def _commit_parts(cards, *, new: int, verb: str | None = None,
                  corrections=()) -> tuple[str, str, str]:
    """The kind, title and body of the commit a family reads in the vault's history.

    A correction's own words go in the body: the card shows what it
    changed, and this is where what was said is kept.
    """
    kind = verb or ("learn" if new == len(cards) else "update")
    title = cards[0].title if len(cards) == 1 else f"{len(cards)} diary entries"
    lines: list[str] = []
    for who, text, _ in corrections:
        lines += [f"{who.title()}:"] + [f"> {ln}" for ln in text.splitlines()] + [""]
    lines += [f"- {c.on.isoformat()} {c.title}" for c in cards]
    return kind, title, "\n".join(lines)


def _commit(memory: MemoryAPI, plan, tree, cards_by_path) -> "set[str]":
    """Write a plan through the curator: one commit per sender, whose
    entries they are, and the removals as the memory bot's. Returns the
    paths that did not land, so the pages can be built from what did."""
    by_sender: dict[str, list[str]] = {}
    for path in plan.writes:
        by_sender.setdefault(cards_by_path[path].sender, []).append(path)

    batches = []
    for sender, paths in sorted(by_sender.items()):
        for i in range(0, len(paths), _COMMIT_BATCH):
            chunk = paths[i:i + _COMMIT_BATCH]
            changes = [put(p, plan.writes[p], base=tree[p][0] if p in tree else "") for p in chunk]
            new = sum(1 for p in chunk if p not in tree)
            batches.append((changes, sender, _commit_parts(
                [cards_by_path[p] for p in chunk], new=new), _SOURCE))
    if plan.deletes:
        count = len(plan.deletes)
        batches.append(([remove(p, base=tree[p][0]) for p in plan.deletes], MEMORY_BOT,
                        ("forget", f"{count} diary entr{'y' if count == 1 else 'ies'} "
                                   "deleted in the room", ""), {}))

    failed: set[str] = set()
    for changes, by, (kind, title, body), trailers in batches:
        try:
            memory.commit(changes, by=by, kind=kind, title=title, body=body, trailers=trailers)
        except (Refused, CuratorDown) as e:
            _err(f"  could not write {len(changes)} card(s): {e}")
            failed.update(c["path"] for c in changes)
    return failed



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
             "late-night entries may land on the wrong day")
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        _err(f"unknown timezone {name!r}, reading timestamps as UTC")
        return timezone.utc


# Flags that consume the token after them, so the room can be picked out
# of the rest without mistaking a flag's value for it.
_TAKES_A_VALUE = ("--burst-window", "--limit", "--preview", "--by", "--event", "--text")


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
    if argv and argv[0] == "correct":
        return await correct(llm, argv[1:])
    if argv and argv[0] == "pages":
        return await pages(llm, argv[1:])
    room_arg = _positional(argv, "memories")
    dry_run = "--dry-run" in argv
    # A machine-readable report of the cards on stdout, for the archivist,
    # which posts them. Progress stays on stderr.
    as_json = "--json" in argv
    # --force re-reads and re-summarises but keeps transcripts: whisper
    # costs minutes of GPU per recording and its output only changes
    # when its config or vocabulary does. --retranscribe is that case.
    force = "--force" in argv
    # --rebuild reads every entry whose card the compile owns under the
    # current prompts and writes the cards that come out different. A
    # reading already made under them costs nothing.
    rebuild = force or "--rebuild" in argv
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
    # A limited run sees part of the room: an entry cut at the edge, a
    # reply without the entry it answers. A card written from that is
    # wrong, and so is every page built from it, so whatever else was
    # asked, a limited run is a dry run.
    if limit and not dry_run:
        _err(f"--limit {limit} reads part of the room: a dry run, nothing is written")
    dry_run = dry_run or bool(limit)
    try:
        preview = int(_opt(argv, "--preview", "0"))
    except ValueError:
        preview = -1
    if "--preview" in argv and preview < 1:
        _err("--preview wants a number of entries")
        return 2
    # A preview writes nothing either: its report is the output.
    dry_run = dry_run or bool(preview)

    zone = _household_zone()
    # Pages render in the household language. Selected once per run.
    diary.configure_language(os.environ.get("LANGUAGE", ""))
    readings_cache, summaries_cache = diary_store.open_stores(
        reading_fingerprint=transcripts.fingerprint(_READ_PROMPT),
        summary_fingerprint=transcripts.fingerprint(_SUMMARY_PROMPT))
    extractions_cache = diary_store.open_extractions()
    homeserver = os.environ.get("MATRIX_HOMESERVER", "").rstrip("/")
    if not homeserver:
        _err("MATRIX_HOMESERVER not set; is core up?")
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
        if preview:
            messages = _newest(messages, preview)
        if not messages and preview:
            await transcriber.aclose()
            print(json.dumps(_preview_report([], {}, set(), room_id=room_id, bucket=bucket,
                                             ontology=_ontology(), language=_language_code())))
            return 0
        if not messages:
            # Still publish. The page is where the wiki's diary link
            # lands, and with nothing recorded it carries the note that
            # explains how to record something.
            _err(f"nothing recorded in {room_arg} yet, publishing an empty diary")
            await transcriber.aclose()
            rc = _publish_pages(diary.pages_for([], bucket=bucket),
                                bucket=bucket, dry_run=dry_run)
            if as_json:
                # The archivist posts from this report, and a run with no
                # report reads to it as a failed compile.
                print(json.dumps({"room_id": room_id, "cards": [], "new": 0,
                                  "kept_as_edited": []}))
            return rc
        _err(f"{len(messages)} message(s) in {room_arg}")

        # Before anything is read or interpreted: the files themselves.
        kept = await _archive_media(
            messages, events, session=session, homeserver=homeserver,
            token=token, room_id=room_id)

        # Transcription first and on its own: every later step reads
        # words, and a recording that cannot be decoded should drop out
        # before the model is asked to interpret its filename.
        vocabulary = _household_vocabulary()
        people = _household_people()
        if vocabulary:
            _err(f"  decoding against: {vocabulary[:90]}...")

        # Transcription dominates runtime, so each recording is logged
        # before the whisper call rather than after. Cached transcripts
        # return immediately and are not logged.
        recordings = [m for m in messages if m.kind == "voice"]
        pending = sum(1 for m in recordings
                      if TRANSCRIPTS.read(m.event_id) is None)
        if recordings:
            _err(f"  {len(recordings)} recording(s), {pending} to decode")

        decoded, heard = [], 0
        for msg in messages:
            if msg.kind != "voice":
                decoded.append(msg)
                continue
            heard += 1
            if TRANSCRIPTS.read(msg.event_id) is None:
                _err(f"  [{heard}/{len(recordings)}] decoding {msg.sender}, "
                     f"{_length(msg.duration_ms)}")
            text = await _transcribe(
                msg, session=session, homeserver=homeserver, token=token,
                transcriber=transcriber, llm=llm, vocabulary=vocabulary,
                people=people, retranscribe=retranscribe)
            if not text.strip():
                record = TRANSCRIPTS.read(msg.event_id) or {}
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
            decoded, llm, None if force else readings_cache)

    await transcriber.aclose()

    entries = diary.compile_entries(decoded, readings,
                                    continues=continues,
                                    refers_to=refers_to,
                                    card_roots=diary.card_threads(events))
    _err(f"{len(entries)} diary entr{'y' if len(entries) == 1 else 'ies'}")
    if preview:
        newest = sorted(entries, key=lambda e: e.at)[-preview:]
        readings_cache.save()
        return await _preview(newest, llm, extractions_cache, bucket=bucket,
                              room_id=room_id, media=kept)

    # ── Cards: one record per entry, in the vault ──
    memory = MemoryAPI.from_container(os.environ)
    try:
        tree = _read_diary_tree(memory, bucket)
    except (Refused, CuratorDown) as e:
        _err(f"could not read the diary records from the memory: {e}; "
             "cards not written, diary pages left as they were")
        return 1
    on_file = {p: t for p, (_, t) in tree.items()}
    cards = await _cards_for(entries, llm, extractions_cache, bucket=bucket,
                             room_id=room_id, media=kept, on_file=on_file,
                             rebuild=rebuild, force=force)
    plan = diary_card.plan(on_file, cards, bucket=bucket, complete=not limit,
                           rebuild=rebuild)
    _err(f"  cards: {len(plan.writes)} to write, {len(plan.deletes)} to remove, "
         f"{len(plan.kept)} the family's and kept")
    for path in plan.kept:
        _err(f"    kept as the family left it: {path}")
    if older := _made_under_older_prompts(entries, on_file, plan, bucket=bucket):
        _err(f"  {older} card{'s were' if older != 1 else ' was'} made under older prompts; "
             "`stack memory diary --preview <n>` shows what the current ones make of the "
             "newest, `stack memory diary --rebuild` writes them all again")
    if limit:
        _err(f"  {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} in the newest "
             f"{limit} messages; a limited run writes no card and removes nothing")

    failed: set[str] = set()
    if dry_run:
        for path in sorted(plan.writes):
            _err(f"    would write {path}")
        for path in plan.deletes:
            _err(f"    would remove {path}")
    elif plan.writes or plan.deletes:
        by_path = {diary_card.card_path(c, bucket=bucket): c for c in cards}
        failed = _commit(memory, plan, tree, by_path)

    records = {p: t for p, (_, t) in tree.items()}
    for path, text in plan.writes.items():
        if path not in failed:
            records[path] = text
    for path in plan.deletes:
        if path not in failed:
            records.pop(path, None)
    readings_cache.save()
    # A dry run publishes nothing. It prints the pages, unless stdout is
    # the JSON report, which they would corrupt.
    rc = 0 if dry_run and as_json else await _project(
        records, llm, summaries_cache, room_id=room_id, bucket=bucket,
        rebuild=force, dry_run=dry_run)
    if as_json:
        print(json.dumps(_report(cards, plan, tree, failed, bucket=bucket,
                                 room_id=room_id,
                                 posted=diary.card_threads(events)),
                         ensure_ascii=False))
    return rc or (1 if failed else 0)


async def _cards_for(entries, llm, extractions_cache, *, bucket: str, room_id: str,
                     media: "dict[str, str]", on_file: "dict[str, str]",
                     rebuild: bool, force: bool) -> list:
    """Each entry's card, the model asked only where a card needs it.

    The model reads an entry with no card yet and one whose card was made
    from an earlier version of it (`diary_card.same_sources`). With
    `rebuild` it reads every entry whose card the compile owns, under the
    current prompts. A card that stands is passed on as it is on file, and
    an entry whose card a person changed is never read: `plan` keeps the
    card whatever the model would say.
    """
    bare = [diary_card.to_card(e, diary_card.Extraction(), room_id=room_id, media=media)
            for e in entries]
    standing = _standing(bare, on_file, rebuild=rebuild)
    to_read = [e for e, card in zip(entries, bare) if card.entry_id not in standing]
    ontology, people, people_section, language_code, declared = _vocabulary(bucket)
    extractions = await _extract_all(
        to_read, llm, extractions_cache, ontology=ontology, people=people, declared=declared,
        vocabulary=ontology.classifier_prompt_section(language_code),
        people_section=people_section, language_code=language_code,
        current=rebuild, force=force)
    read = {diary_card.entry_id_for(e.event_ids[0]): (e, x)
            for e, x in zip(to_read, extractions)}
    return [diary_card.to_card(*read[card.entry_id], room_id=room_id, media=media)
            if card.entry_id in read else standing[card.entry_id] or card
            for card in bare]


def _standing(cards, on_file: "dict[str, str]", *, rebuild: bool) -> "dict[str, object]":
    """The entries whose card on file stays as it is, by entry id.

    The value is the card as it is on file, for the compile to carry on
    with, or None for a card a person changed: the compile keeps it, and
    goes on with the entry as it is in the room so `plan` can tell
    whether the room moved on under it.
    """
    by_entry = diary_card.cards_on_file(on_file)
    out: dict[str, object] = {}
    for card in cards:
        _, text = by_entry.get(card.entry_id, ("", ""))
        if not text:
            continue
        if diary_card.edited_by_hand(text):
            out[card.entry_id] = None
            continue
        before = diary_card.parse(text)
        if not rebuild and diary_card.same_sources(before, card):
            out[card.entry_id] = before
    return out


def _made_under_older_prompts(entries, on_file: "dict[str, str]", plan, *, bucket: str) -> int:
    """How many cards the compile owns and keeps were made under a prompt
    template other than the one an entry is read under now."""
    by_entry = diary_card.cards_on_file(on_file)
    older = 0
    for entry in entries:
        path, text = by_entry.get(diary_card.entry_id_for(entry.event_ids[0]), ("", ""))
        if not text or path in plan.writes or path in plan.deletes:
            continue
        if diary_card.edited_by_hand(text):
            continue
        if diary_card.parse(text).prompt_template != _template(entry):
            older += 1
    return older


# ── A preview ─────────────────────────────────────────────────────────
#
# What new prompts do to the cards, shown on a few entries before a
# compile applies them. The preview reads the newest entries the way the
# compile would and compares each card it makes with the card on file.
# It writes nothing; the readings it pays for are kept, so the compile
# that follows does not pay for them again.

# Entries read before the ones a preview shows, so an entry at the edge
# still has the half or the photo it continues. Their cards are not shown.
_PREVIEW_RUN_UP = _OVERLAP


def _newest(messages, count: int) -> list:
    """The newest part of the room, holding its `count` newest entries.

    Counted in messages that can start an entry: a reply or a message in
    a thread never does, and the archivist's own posts are already gone
    (`diary.resolve`). A reply in the part whose entry is older than the
    part is left out: on its own it would read as an entry of its own.
    """
    starts = [i for i, m in enumerate(messages) if not (m.reply_to or m.thread_root)]
    take = count + _PREVIEW_RUN_UP
    if len(starts) <= take:
        return list(messages)
    part = messages[starts[-take]:]
    inside = {m.event_id for m in part}
    return [m for m in part if (m.reply_to or m.thread_root or m.event_id) in inside]


async def _preview(entries, llm, extractions_cache, *, bucket: str, room_id: str,
                   media: "dict[str, str]") -> int:
    """Print each entry's card as it is on file and as a rebuild makes it now."""
    try:
        tree = _read_diary_tree(MemoryAPI.from_container(os.environ), bucket)
    except (Refused, CuratorDown) as e:
        _err(f"could not read the diary records from the memory: {e}; no cards to compare with")
        return 1
    on_file = {p: t for p, (_, t) in tree.items()}
    cards = await _cards_for(entries, llm, extractions_cache, bucket=bucket,
                             room_id=room_id, media=media, on_file=on_file,
                             rebuild=True, force=False)
    # A rebuild's card has the sources of the entry, so it says what a
    # compile keeps as well as a card read without the model would.
    compile_keeps = set(_standing(cards, on_file, rebuild=False))
    print(json.dumps(_preview_report(cards, on_file, compile_keeps, room_id=room_id,
                                     bucket=bucket, ontology=_ontology(),
                                     language=_language_code()), ensure_ascii=False))
    return 0


def _preview_report(cards, on_file: "dict[str, str]", compile_keeps: "set[str]", *,
                    room_id: str, bucket: str, ontology, language: str) -> dict:
    """Each card as it is on file (`before`, None for a new entry) and as
    the current prompts make it (`after`), with what a rebuild does to it
    (`state`: `new`, `changed`, `unchanged`, or `kept` for a card a person
    changed) and whether the next compile writes it (`next_compile`):
    a compile rewrites a card only when its entry changed. Topics show as
    the household reads them (`_topic_labels`)."""
    plan = diary_card.plan(on_file, cards, bucket=bucket, complete=False, rebuild=True)
    by_entry = diary_card.cards_on_file(on_file)
    out = []
    for card in cards:
        path = diary_card.card_path(card, bucket=bucket)
        _, old = by_entry.get(card.entry_id, ("", ""))
        before = diary_card.parse(old) if old else None
        if path in plan.writes:
            state, after = ("changed" if before else "new"), diary_card.parse(plan.writes[path])
        elif before is not None and diary_card.edited_by_hand(old):
            state, after = "kept", before
        else:
            state, after = "unchanged", before or card
        out.append({"entry_id": card.entry_id, "path": path, "state": state,
                    "next_compile": "keeps" if card.entry_id in compile_keeps else "writes",
                    "before": _readable(before, ontology, language) if before else None,
                    "after": _readable(after, ontology, language)})
    return {"room_id": room_id, "entries": out}


def _readable(card, ontology, language: str) -> dict:
    """The parts of a card a person reads."""
    return {"date": card.on.isoformat(), "title": card.title,
            "description": card.description, "people": card.persons,
            "topics": _topic_labels(card.tags, ontology, language),
            "summary": card.summary, "facts": card.facts, "quotes": card.quotes,
            "replies": [f"{who}: {text}" for who, text in card.replies],
            "words": card.body}


def _language_code() -> str:
    """The household language's code, `en` when none is set."""
    return (os.environ.get("LANGUAGE") or "en").strip().lower()[:2]


def _ontology():
    """The topics the household tags with: the memory's own ontology, else the seed."""
    vault_dir = Path(os.environ.get("MEMORY_VAULT_DIR", ""))
    return get_ontology(vault_dir if vault_dir.is_dir() else None)


def _topic_labels(tags, ontology, language: str) -> list[str]:
    """A card's topics as a person reads them, and as the model reads them
    beside the vocabulary it is offered: a topic's label in the household
    language (`stack.tags.tag_label`), a keyword as it is written. A card
    tags a topic by its id (ADR-017 §4); a `Person:` tag, from before
    people had ids, names no topic."""
    return [tag_label(t, ontology, language) for t in tags if not t.startswith("Person: ")]


def _vocabulary(bucket: str):
    """What the model is offered and held to: the ontology, the family's
    names (canonical form by every known name), the family block for the
    prompt, the household language code, and the declared people the
    names resolve to ids through (None until their files exist)."""
    language_code = _language_code()
    ontology = _ontology()
    # Person pages are generated, so they live in the brain, not the vault.
    brain_dir = Path(os.environ.get("BRAIN_REPO_DIR", ""))
    persons = load_persons_from_vault(brain_dir, bucket) if brain_dir.is_dir() else []
    declared = _declared()
    if declared is not None:
        persons = _declared_persons(declared, persons)
    people = diary.card_people(persons, _household_people())
    # The pages describe the people they know; the rest of the household
    # joins the list by name, so the model can still say a note is about them.
    others = sorted(set(people.values()) - {p.canonical for p in persons})
    if persons:
        section = persons_prompt_section(persons) + "".join(f"\n  - {n}" for n in others)
    else:
        section = "Family members: " + ", ".join(others)
    return ontology, people, section, language_code, declared


def _report_unknown(entry_id: str, extraction) -> None:
    """Name each household name no declared person goes by: it stays off the card."""
    for name in extraction.unknown:
        _err(f"  {entry_id}: no declared person goes by {name!r}, left off the card")


async def _project(records, llm, summaries_cache, *, room_id: str,
                   bucket: str, rebuild: bool, dry_run: bool,
                   only: "list[str] | None" = None) -> int:
    """Build and publish the diary pages from the records in the vault.

    `records` maps each record's path to its content, as the vault holds
    it after this run's writes. The pages are a view of these and of
    nothing else, so every correction and hand edit shows.

    Without `llm`, a month whose entries changed keeps the paragraph last
    written for it. `only` (months, `2026-10`) publishes those months'
    pages, their years' and the front page, and asks the model about no
    other month; None publishes every page.
    """
    on_file, entries = _entries_on_file(records)
    summaries = await _summaries(entries, llm, summaries_cache, bucket=bucket,
                                 rebuild=rebuild, only=only)

    pages = diary.pages_for(entries, bucket=bucket, room_id=room_id, summaries=summaries,
                            media=diary_card.media_of(on_file))
    if only is not None:
        wanted = {f"{diary.DIARY_DIR}/about.md"}
        for month in only:
            year, _, number = month.partition("-")
            wanted |= {f"{diary.DIARY_DIR}/{year}/about.md", f"{diary.DIARY_DIR}/{year}/{number}.md"}
        pages = [page for page in pages if page.path in wanted]
    return _publish_pages(pages, bucket=bucket, dry_run=dry_run)


def _entries_on_file(records) -> tuple[list, list]:
    """The cards in `records` (path to text) and their entries, in date order."""
    on_file = [diary_card.parse(t) for t in records.values()]
    versions = {c.card_id: card_version(t.encode("utf-8"))
                for c, t in zip(on_file, records.values())}
    entries = sorted((replace(e, version=versions.get(e.card_id, ""))
                      for e in map(diary_card.to_entry, on_file)),
                     key=lambda e: (e.on, e.at))
    return on_file, entries


async def _summaries(entries, llm, summaries_cache, *, bucket: str, rebuild: bool,
                     only: "list[str] | None") -> dict[str, str]:
    """The paragraph opening each month, by month (`2026-10`).

    The cached one when the month's entries are the ones it was written
    from; otherwise the model's, and without `llm` the last one written.
    A month outside `only` is never sent to the model.
    """
    months: dict[str, list] = {}
    for entry in entries:
        key = f"{diary.year_key(entry.on)}-{diary.month_key(entry.on)}"
        months.setdefault(key, []).append(entry)

    summaries = {}
    for key, in_month in sorted(months.items()):
        if only is not None and key not in only:
            summaries[key] = summaries_cache.latest(key)
            continue
        digest = diary.month_digest(in_month)
        cached = "" if rebuild else summaries_cache.get(key, digest)
        if cached or llm is None:
            summaries[key] = cached or summaries_cache.latest(key)
            continue
        _err(f"  summarising {in_month[0].on.strftime('%B %Y')}")
        summaries[key] = await _summarise(in_month, llm, bucket=bucket)
        if summaries[key]:
            summaries_cache.put(key, digest, summaries[key])
            summaries_cache.save()
    summaries_cache.save()
    return summaries


async def pages(llm, argv: list[str]) -> int:
    """`diary pages [--fast] [--month <YYYY-MM>]...`: the diary pages, from the records on file.

    Reads the diary records from the memory repo's working copy, not the
    room: what the curator runs when a diary card was pushed, a hand edit
    or a correction. `--fast` asks no model; a month whose entries changed
    keeps its paragraph until the next compile with one.

    With the model, the month's paragraph is written first and the pages
    after, from the records as they are by then, under the brain's writer
    lock: the model's turn can take minutes, and a compile without the
    model may have written newer entries meanwhile.
    """
    bucket = os.environ.get("SHARED_BUCKET", "family")
    diary.configure_language(os.environ.get("LANGUAGE", ""))
    vault = Path(os.environ.get("MEMORY_VAULT_DIR", ""))
    _, summaries_cache = diary_store.open_stores(
        summary_fingerprint=transcripts.fingerprint(_SUMMARY_PROMPT))
    months = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--month"] or None
    if "--fast" not in argv:
        _, entries = _entries_on_file(_records_on_file(vault, bucket))
        await _summaries(entries, llm, summaries_cache, bucket=bucket, rebuild=False,
                         only=months)
    with brain_lock.held(Path(os.environ.get("BRAIN_REPO_DIR", ""))):
        records = _records_on_file(vault, bucket)
        room_id = next((r for r in (_room_of(diary_card.parse(t)) for t in records.values())
                        if r), "")
        return await _project(records, None, summaries_cache, room_id=room_id, bucket=bucket,
                              rebuild=False, dry_run="--dry-run" in argv, only=months)


def _records_on_file(vault: Path, bucket: str) -> dict[str, str]:
    """Every diary record in the memory repo's working copy: path to text."""
    root = vault / bucket / diary.DIARY_DIR / diary_card.ENTRIES_DIR
    return {path.relative_to(vault).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(root.rglob("*.md"))}


def _card_json(card, *, path: str, room_id: str) -> dict:
    """A card as the archivist posts it.

    The notice shows no topics (`diary_room.card_text`): `tags` rides in
    the notice's event data as the card holds it, topic ids, like `persons`.
    """
    return {
        "entry_id": card.entry_id, "card_id": card.card_id, "path": path,
        "root": card.event_ids[0], "room_id": room_id,
        "date": card.on.isoformat(), "date_basis": card.confidence,
        "date_label": diary.day_label(card.on), "at": card.at,
        "title": card.title, "summary": card.summary,
        "facts": card.facts, "persons": card.persons,
        "tags": [t for t in card.tags if not t.startswith("Person: ")],
    }


def _report(cards, plan, tree, failed, *, bucket: str, room_id: str,
            posted: "set[str]") -> dict:
    """What this run filed, for whoever posts the cards to the family.

    Every card of the run: `new`, `changed` or `unchanged`, and whether
    its thread already holds a card notice (`posted`), so a card filed by
    a run nobody announced still gets posted. `root` is the entry's first
    message, which never changes, so a card's thread never moves. A card
    the family owns is reported as they left it. Cards whose write failed
    are left out: announcing them would promise something the vault does
    not hold.
    """
    on_file = {}
    for _, text in tree.values():
        before = diary_card.parse(text)
        on_file[before.entry_id] = before
    out = []
    for card in cards:
        path = diary_card.card_path(card, bucket=bucket)
        if path in failed:
            continue
        before = on_file.get(card.entry_id)
        if path in plan.writes:
            # The compile's card gets its id in the plan; what was written has it.
            written = diary_card.parse(plan.writes[path])
            state = "changed" if before is not None else "new"
            shown = replace(card, card_id=written.card_id)
        else:
            state, shown = "unchanged", (before or card)
        out.append({**_card_json(shown, path=path, room_id=room_id),
                    "state": state, "posted": card.event_ids[0] in posted})
    return {"room_id": room_id, "cards": out,
            "new": sum(1 for c in out if c["state"] == "new"),
            "kept_as_edited": list(plan.kept)}


# ── Correcting a card ─────────────────────────────────────────────────
#
# A family member replies in a card's thread: "that was Lisa, not Bart".
# The archivist hands the words here. The correction is applied once, to
# the card as it is in the vault, and committed by the person who made
# it, their words in the commit. From then on the card is the family's
# and the compile leaves it alone.

_CORRECT_TOKENS = 900

_CORRECT_PROMPT = """\
A family member corrected an entry in the family archive. Apply the
correction to the entry's card and return the corrected card. Change
what the correction says is wrong and anything that follows from it;
keep everything else as it is. The correction is right wherever it
disagrees with the card or with the entry's words.

{vocabulary}

{people}

The card as it is now:
{card}

The entry's words:
{words}

The correction, from {who}:
{correction}

Reply with a JSON object with these keys, all in {language} except the
names of people and topics:
"title", "description", "summary" (one to three short sentences; when
the card names the people it could be, joined with "or", and the
correction says who it was, name that person and drop the others here,
in the title and in "persons"),
"facts" (each "Label: value", labelled by who or what it is about, such
as a family member's name or "Place", never by the kind of fact such as
"Action", "Event", "Date" or "Person"; milestones first; at most five;
none for what the summary already says), "persons" (names from the
family list only), "topics" (topics
from the vocabulary only), "quotes" (passages copied word for word from
the entry's words; keep the card's quotes unless the correction touches
them), "date": the day the entry happened as YYYY-MM-DD when the
correction states it, otherwise null, and "addressee": whom the entry is
spoken to when the correction says so (a name from the family list, or
the correction's own words such as "the kids"), otherwise null.
"""


def _card_prompt(card, declared: list[DeclaredPerson] | None, *,
                 ontology, language: str) -> str:
    """The card as the correction prompt shows it: its people by name and its
    topics by their label in the household language, not by id."""
    names = {p.id: p.first_name for p in declared or []}
    lines = [f"Title: {card.title}", f"Date: {card.on.isoformat()}",
             f"Spoken to: {card.addressee or 'no one in particular'}",
             f"Description: {card.description}",
             f"People: {', '.join(names.get(p) or p for p in card.persons)}",
             f"Topics: {', '.join(_topic_labels(card.tags, ontology, language))}",
             f"Summary: {card.summary}"]
    lines += ["Facts:"] + [f"- {f}" for f in card.facts]
    if card.quotes:
        lines += ["Quotes:"] + [f"- {q}" for q in card.quotes]
    return "\n".join(lines)


def _words_prompt(card) -> str:
    lines = [f"Recorded by {card.sender.title()}:", card.body.strip() or "(no words)"]
    for who, text in card.replies:
        lines += ["", f"{who.title()} replied: {text}"]
    return "\n".join(lines)


def _room_of(card) -> str:
    """The room a card came from, read off its permalink."""
    tail = card.resource.split("/#/", 1)[-1] if card.resource else ""
    return tail.split("/", 1)[0] if tail.startswith("!") else ""


async def correct(llm, argv: list[str]) -> int:
    """`diary correct <entry_id> --by <person> --event <event_id> --text <words>`

    Prints the corrected card as JSON. Running it again for the same
    correction event changes nothing: the event id is on the card once
    it is applied.
    """
    entry_id = argv[0] if argv and not argv[0].startswith("-") else ""
    by, event_id, words = (_opt(argv, "--by", ""), _opt(argv, "--event", ""),
                           _opt(argv, "--text", "").strip())
    if not (entry_id and by and event_id and words):
        _err("usage: diary correct <entry_id> --by <person> --event <event_id> --text <words>")
        return 2
    bucket = os.environ.get("SHARED_BUCKET", "family")
    diary.configure_language(os.environ.get("LANGUAGE", ""))

    memory = MemoryAPI.from_container(os.environ)
    try:
        tree = _read_diary_tree(memory, bucket)
    except (Refused, CuratorDown) as e:
        _err(f"could not read the diary records from the memory: {e}")
        return 1
    found = [(path, sha, text) for path, (sha, text) in tree.items()
             if diary_card.parse(text).entry_id == entry_id]
    if not found:
        _err(f"no diary card with entry id {entry_id}")
        return 1
    old_path, old_sha, old_text = found[0]
    card = diary_card.parse(old_text)
    room_id = _room_of(card)
    if event_id in card.event_ids:
        _err(f"  {event_id} is already applied to {old_path}")
        print(json.dumps({**_card_json(card, path=old_path, room_id=room_id),
                          "changed": False}, ensure_ascii=False))
        return 0

    ontology, people, people_section, language_code, declared = _vocabulary(bucket)
    prompt = _CORRECT_PROMPT.format(
        vocabulary=ontology.classifier_prompt_section(language_code),
        people=people_section,
        card=_card_prompt(card, declared, ontology=ontology, language=language_code),
        words=_words_prompt(card), who=by.title(), correction=words,
        language=_household_language())
    try:
        raw = json.loads(await llm.complete(
            "classifier", prompt, json_mode=True, temperature=0,
            max_tokens=_CORRECT_TOKENS, timeout=_EXTRACT_TIMEOUT_S))
    except (LLMError, json.JSONDecodeError, TypeError) as e:
        _err(f"could not apply the correction: {e}")
        return 1
    extraction = diary_card.extraction_from(
        raw, ontology=ontology, language=language_code, people=people,
        declared=declared, model=os.environ.get("AI_DEFAULT_MODEL", ""))
    _report_unknown(card.entry_id, extraction)
    extraction = diary_card.Extraction(**{
        **extraction.__dict__,
        "quotes": _sure_quotes(extraction.quotes, card.event_ids)})
    fixed = diary_card.correct(card, extraction, event_id=event_id)
    new_path = diary_card.card_path(fixed, bucket=bucket)
    text = diary_card.render(fixed, family_owned=True)

    if new_path == old_path:
        changes = [put(old_path, text, base=old_sha)]
    else:
        changes = [remove(old_path, base=old_sha), put(new_path, text, base="")]
    kind, title, body = _commit_parts([fixed], new=0, verb="correct",
                                      corrections=[(by, words, event_id)])
    try:
        memory.commit(changes, by=by, kind=kind, title=title, body=body, trailers=_SOURCE)
    except (Refused, CuratorDown) as e:
        _err(f"could not write the corrected card: {e}")
        return 1
    _err(f"  corrected {new_path}" + (f" (moved from {old_path})" if new_path != old_path else ""))

    records = {p: t for p, (_, t) in tree.items() if p != old_path}
    records[new_path] = text
    _, summaries_cache = diary_store.open_stores(
        summary_fingerprint=transcripts.fingerprint(_SUMMARY_PROMPT))
    rc = await _project(records, llm, summaries_cache, room_id=room_id,
                        bucket=bucket, rebuild=False, dry_run=False)
    print(json.dumps({**_card_json(fixed, path=new_path, room_id=room_id),
                      "changed": True, "moved_from": old_path if new_path != old_path else ""},
                     ensure_ascii=False))
    return rc


def _publish_pages(pages, *, bucket: str, dry_run: bool) -> int:
    """Write the compiled pages into the brain working copy.

    One publisher for both exits: a full compile and the empty diary
    reach the wiki the same way, so the page a family lands on has the
    same frontmatter and the same splice contract either way. Each page
    is a view of the entries it shows.
    """
    if dry_run:
        for page in pages:
            print(f"\n{'=' * 70}\n{bucket}/{page.path}\n{'=' * 70}\n{page.body}")
        return 0

    rc = 0
    for page in pages:
        rc |= wiki._publish(
            page.body, target_path=f"{bucket}/{page.path}",
            view=wiki.ViewFields(page.view, title=page.title,
                                 sources=[(e.card_id, e.version) for e in page.entries]),
        )
    return rc
