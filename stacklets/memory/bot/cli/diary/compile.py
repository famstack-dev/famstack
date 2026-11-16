"""`stack memory diary`: the memories room compiled into cards and pages.

    rc = await run(llm, ["--dry-run"])

`run` reads the room, decodes its recordings, groups its messages into
entries, writes one card per entry through the curator and publishes
the pages. Each step is a function of its own module; `run` names them
in order.
"""

from __future__ import annotations

import json
import os
from typing import NamedTuple

# httpx rather than aiohttp: the OpenAI SDK already pulls it into every
# container that can talk to a model, so the compiler runs unchanged in
# the bot-runner and in the curator that schedules it nightly.
import httpx
from stack import clock
from stack.ai import transcripts
from stack.ai.client import LLMError, Transcriber
from stack.memory import CuratorDown, MemoryAPI, Refused

from ... import diary_store
from ...diary.card import entry_id_for
from ...diary.entries import compile_entries
from ...diary.grouping import card_threads, filed_groups, group_room
from ...diary.messages import resolve
from ...diary.pages import pages_for
from ...diary.plan import plan as plan_files
from ...diary.texts import configure_language
from .cards import cards_for, made_under_older_prompts
from .correction import correct
from .household_files import household_language_code, household_ontology
from .options import Options, options
from .preview import newest_entries, preview_cards, preview_report
from .progress import err
from .projection import project, publish_pages, pages
from .reading import READ_PROMPT, read_room
from .report import diary_report
from .room import admin_token, history, keep_files, remember_room, resolve_room
from .store import read_diary_tree, records_after, write
from .summary import SUMMARY_PROMPT
from .transcribe import decode_room

# ── The command ───────────────────────────────────────────────────────


def _household_zone():
    """The clock the family keeps, for turning timestamps into days (`stack.clock`).

    Falls back with a warning rather than failing (UTC for a zone the
    system does not know, the system's own zone when none is set): a diary
    with some entries an hour either side of midnight is worth more than
    no diary, and the operator can see why in the output.
    """
    found = clock.household()
    if found.reason == "unset":
        err("TIMEZONE not set, reading timestamps in the system's zone; "
             "late-night entries may land on the wrong day")
    elif found.reason == "unknown":
        err(f"unknown timezone {found.name!r}, reading timestamps as UTC")
    return found.zone


async def run(llm, argv: list[str]) -> int:
    """`stack memory diary [<room>] ...`, and its subcommands `correct` and
    `pages`. Returns the exit code: 0, 1 for a failure, 2 for a bad flag."""
    if argv and argv[0] == "correct":
        return await correct(llm, argv[1:])
    if argv and argv[0] == "pages":
        return await pages(llm, argv[1:])
    opts = options(argv)
    if opts is None:
        return 2

    zone = _household_zone()
    # Pages render in the household language. Selected once per run.
    configure_language(os.environ.get("LANGUAGE", ""))
    caches = _open_caches()
    homeserver = os.environ.get("MATRIX_HOMESERVER", "").rstrip("/")
    if not homeserver:
        err("MATRIX_HOMESERVER not set; is core up?")
        return 1
    bucket = os.environ.get("SHARED_BUCKET", "family")

    try:
        transcriber = Transcriber.from_env(namespace="memory-diary")
    except LLMError as e:
        err(f"no transcription available: {e}")
        return 1
    try:
        room = await _read(llm, opts, caches, zone=zone, homeserver=homeserver,
                           transcriber=transcriber, bucket=bucket)
    finally:
        await transcriber.aclose()
    if isinstance(room, int):
        return room
    return await _compile(llm, opts, caches, room, bucket=bucket)


# ── Reading the room ──────────────────────────────────────────────────


class _Caches(NamedTuple):
    """The caches of the model's work: readings, summaries, extractions."""

    readings: object
    summaries: object
    extractions: object


def _open_caches() -> _Caches:
    """The caches, each keyed by the prompt its entries were made under."""
    readings, summaries = diary_store.open_stores(
        reading_fingerprint=transcripts.fingerprint(READ_PROMPT),
        summary_fingerprint=transcripts.fingerprint(SUMMARY_PROMPT))
    return _Caches(readings, summaries, diary_store.open_extractions())


class _Room(NamedTuple):
    """The room as a compile read it: its messages decoded, and the model's
    reading of each."""

    room_id: str
    events: list
    messages: list
    readings: dict
    kept: dict


async def _read(llm, opts: Options, caches: _Caches, *, zone, homeserver: str,
                transcriber, bucket: str) -> "_Room | int":
    """The room, its files kept, its recordings decoded and its messages read.

    Returns the exit code instead when the room cannot be read, or when it
    holds nothing and the pages for that are published.
    """
    # Recordings can be tens of megabytes; the default five seconds is
    # for APIs, not for media.
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as session:
        try:
            token = await admin_token(session, homeserver)
            room_id = await resolve_room(session, homeserver, token, opts.room)
            events = await history(session, homeserver, token, room_id,
                                    limit=opts.limit)
        except (RuntimeError, httpx.HTTPError) as e:
            err(str(e))
            return 1

        messages = resolve(events, zone=zone)
        if opts.preview:
            messages = newest_entries(messages, opts.preview, window_s=opts.window)
        if not messages:
            return _nothing_recorded(opts, room_id=room_id, bucket=bucket)
        err(f"{len(messages)} message(s) in {opts.room}")

        # Before anything is read or interpreted: the files, and the day each was taken.
        messages, kept = await keep_files(messages, events, session=session,
                                           homeserver=homeserver, token=token, room_id=room_id)
        decoded = await decode_room(messages, session=session, homeserver=homeserver,
                                     token=token, transcriber=transcriber, llm=llm,
                                     retranscribe=opts.retranscribe)
        readings = await read_room(decoded, llm, None if opts.force else caches.readings)
    return _Room(room_id, events, decoded, readings, kept)


def _nothing_recorded(opts: Options, *, room_id: str, bucket: str) -> int:
    """A room with no message: an empty preview, or the empty diary published."""
    if opts.preview:
        print(json.dumps(preview_report([], {}, set(), room_id=room_id, bucket=bucket,
                                         ontology=household_ontology(), language=household_language_code())))
        return 0
    # Still publish. The page is where the wiki's diary link
    # lands, and with nothing recorded it carries the note that
    # explains how to record something.
    err(f"nothing recorded in {opts.room} yet, publishing an empty diary")
    rc = publish_pages(pages_for([], bucket=bucket),
                        bucket=bucket, dry_run=opts.dry_run)
    if opts.as_json:
        # The archivist posts from this report, and a run with no
        # report reads to it as a failed compile.
        print(json.dumps({"room_id": room_id, "cards": [], "new": 0,
                          "kept_as_edited": []}))
    return rc


# ── Compiling the cards ───────────────────────────────────────────────


async def _compile(llm, opts: Options, caches: _Caches, room: _Room, *, bucket: str) -> int:
    """The room's entries as cards in the memory, and the pages from them."""
    # The cards on file come first: their messages keep the grouping they
    # were filed with (`filed_groups`), so a compile never
    # moves a message off a card, also one a person corrected.
    memory = MemoryAPI.from_container(os.environ)
    try:
        tree = read_diary_tree(memory, bucket)
    except (Refused, CuratorDown) as e:
        err(f"could not read the diary records from the memory: {e}; "
             "cards not written, diary pages left as they were")
        return 1
    on_file = {p: t for p, (_, t) in tree.items()}
    entries = _entries(room, on_file, window_s=opts.window)
    if opts.preview:
        newest = sorted(entries, key=lambda e: e.at)[-opts.preview:]
        caches.readings.save()
        return await preview_cards(newest, llm, caches.extractions, on_file=on_file, bucket=bucket,
                              room_id=room.room_id, media=room.kept)
    remember_room(room.room_id)

    cards = await cards_for(entries, llm, caches.extractions, bucket=bucket,
                             media=room.kept, on_file=on_file, rebuild=opts.rebuild,
                             force=opts.force)
    plan = plan_files(on_file, cards, bucket=bucket, complete=not opts.limit,
                      rebuild=opts.rebuild)
    _say_plan(plan, entries, on_file, opts, bucket=bucket)
    failed = write(memory, plan, tree, cards, dry_run=opts.dry_run, bucket=bucket)
    caches.readings.save()
    # A dry run publishes nothing. It prints the pages, unless stdout is
    # the JSON report, which they would corrupt.
    rc = 0 if opts.dry_run and opts.as_json else await project(
        records_after(tree, plan, failed), llm, caches.summaries, room_id=room.room_id,
        bucket=bucket, rebuild=opts.force, dry_run=opts.dry_run)
    if opts.as_json:
        ended_by = {entry_id_for(e.event_ids[0]): e.ended_by for e in entries}
        print(json.dumps(diary_report(cards, plan, tree, failed, bucket=bucket,
                                 room_id=room.room_id,
                                 posted=card_threads(room.events), ended_by=ended_by),
                         ensure_ascii=False))
    return rc or (1 if failed else 0)


def _entries(room: _Room, on_file: "dict[str, str]", *, window_s: float) -> list:
    """The room's entries: its messages grouped, each group one entry."""
    groups = group_room(room.messages, room.readings, window_s=window_s,
                        filed=filed_groups(on_file, room.messages),
                        card_roots=card_threads(room.events),
                        now_ms=int(clock.now().timestamp() * 1000))
    entries = compile_entries(groups, room.readings)
    err(f"{len(entries)} diary entr{'y' if len(entries) == 1 else 'ies'}")
    return entries


def _say_plan(plan, entries, on_file: "dict[str, str]", opts: Options, *, bucket: str) -> None:
    """Say what the compile writes, removes and keeps, before it does."""
    err(f"  cards: {len(plan.writes)} to write, {len(plan.deletes)} to remove, "
         f"{len(plan.kept)} the family's and kept")
    for path in plan.kept:
        err(f"    kept as the family left it: {path}")
    if older := made_under_older_prompts(entries, on_file, plan, bucket=bucket):
        err(f"  {older} card{'s were' if older != 1 else ' was'} made under older prompts; "
             "`stack memory diary --preview <n>` shows what the current ones make of the "
             "newest, `stack memory diary --rebuild` writes them all again")
    if opts.limit:
        err(f"  {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} in the newest "
             f"{opts.limit} messages; a limited run writes no card and removes nothing")
