"""A note's identity: found again by the messages it was filed from, and a free name for a new one.

    stored = await on_file(memory, after, entity="marge", capture_id="$grill", by="@marge:simpson")
    stored.path, stored.card_id     # the card on file, or stored is None
    free_name("marge/notes/2026/11/grill-0c401e.md", taken=memory.paths("marge/notes/2026/11"))
    # 'marge/notes/2026/11/grill-0c401e-2.md' when the first name is taken

A capture is found again by the messages it was filed from: the ids its
sidecar keeps for its originals (ADR-018), or the `capture_id` of an old
note filed before sidecars. Never by a hash of its words or its link: two
notes with the same link and title would share a path and replace each
other, the same words sent twice would be one card, and a replay after a
hand edit would rewrite the edit.

The curator answers the lookup from its own clone (`MemoryAPI.filed_from`),
which every write goes through, so a capture filed a moment ago is found
and a replay right after its filing writes nothing. A reply's id finds
nothing, because a reply corrects a card and is not what it was filed from.

Where it fits: the filing core (`stack.filing.file`) asks `on_file` before
the model, for a note's first filing only; a correction reaches its card by
its path, and a bookmark or an email keeps its own identity (the link, the
thread). The write (`stack.filing.write.write_capture`) asks
`MemoryAPI.filed_from` again right before it writes, and else writes a new
note at `free_name` on an empty base, so a file that appeared meanwhile is
never overwritten.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from itertools import count
from typing import TYPE_CHECKING, NamedTuple

from loguru import logger

from ..frontmatter import FrontmatterError, parse
from ..memory import CuratorDown, File, MemoryAPI, Refused

if TYPE_CHECKING:   # the write imports this module for its names
    from .write import AfterSteps

__all__ = ["OnFile", "free_name", "notes_of", "on_file", "stored_filing"]

# How many names a new note tries (`name.md`, `name-2.md`, ...) before it
# gives up: each try follows a file that appeared at the name a moment ago,
# which on a family server happens once in a while, never ten times in a row.
NAMES_TRIED = 10


class OnFile(NamedTuple):
    """A capture the memory holds already: where its card is, its `card_id`,
    what the card holds (title, people by id, tags) and the day it was filed.

        stored = await on_file(memory, mirror, entity="marge", capture_id="$grill", by="marge")
        stored.path, stored.card_id   # ('marge/notes/2026/11/grill-charcoal-0c401e.md', 'note-…')
        stored.card                   # {'title': 'Grill charcoal', 'persons': ['marge'], 'tags': []}
        stored.captured_at            # '2026-11-22'
    """

    path: str
    card_id: str
    card: dict
    captured_at: str


def notes_of(entity: str) -> str:
    """The folder a capture's notes are in, at any depth below it.

        notes_of("marge")              # 'marge/notes'
        notes_of("homer/camping")      # 'homer/camping/notes', a personal topic
    """
    return f"{entity}/notes"


async def on_file(memory: MemoryAPI, after: "AfterSteps", *, entity: str, capture_id: str,
                  by: str) -> OnFile | None:
    """The note filed from the message `capture_id` under `entity`, or None when none is.

    Asks the curator (`MemoryAPI.filed_from`). For a card on file, the
    after-steps run again with no items (`AfterSteps.after_filing`): the
    topic's card is declared as for a first filing, and the to-do list is
    left as it is. Nothing else is written, and the card's words, time and
    hash are not compared, so a hand edit stays.

    A curator that cannot answer is logged and gives None: the filing goes
    on, and its write, on an empty base, cannot overwrite a card.

        stored = await on_file(memory, mirror, entity="marge", capture_id="$grill",
                               by="@marge:simpson")
        stored.path     # 'marge/notes/2026/11/grill-charcoal-0c401e.md'
    """
    if not capture_id:
        return None
    try:
        found = await asyncio.to_thread(memory.filed_from, capture_id, under=notes_of(entity))
    except (Refused, CuratorDown) as e:
        logger.warning("[filing] could not ask whether {} is on file: {}", capture_id, e)
        return None
    if found is None:
        return None
    stored = stored_filing(found)
    await after.after_filing(found.path, {"title": stored.card["title"], "action_items": []},
                             by=by, previous_path="")
    logger.info("[filing] {} is on file as {}: nothing written", capture_id, found.path)
    return stored


def stored_filing(found: File) -> OnFile:
    """The card at `found` as a replay names it: its path, `card_id`, title,
    people and tags as the card has them, and the day it was filed.

    A card whose frontmatter does not parse is named by its path alone.

        stored_filing(File("marge/notes/2026/11/grill-0c401e.md", text, version)).card
        # {'title': 'Grill charcoal', 'persons': ['marge'], 'tags': ['grill']}
    """
    try:
        head = parse(found.text)
    except FrontmatterError:
        head = {}
    card = {"title": str(head.get("title") or ""),
            "persons": [str(p) for p in head.get("persons") or []],
            "tags": [str(t) for t in head.get("tags") or []]}
    return OnFile(found.path, str(head.get("card_id") or ""), card,
                  str(head.get("date") or head.get("created") or ""))


def free_name(target_path: str, taken: Iterable[str]) -> str:
    """The first of `<name>.md`, `<name>-2.md`, `<name>-3.md` that is free.

    A name is free when neither the card nor its sidecar (`<name>.json`)
    is among the `taken` paths: a sidecar left without its card still
    names that card's sources.

        free_name("marge/notes/2026/11/grill-0c401e.md", [])
        # 'marge/notes/2026/11/grill-0c401e.md'
        free_name("marge/notes/2026/11/grill-0c401e.md",
                  ["marge/notes/2026/11/grill-0c401e.md", "marge/notes/2026/11/grill-0c401e-2.json"])
        # 'marge/notes/2026/11/grill-0c401e-3.md'
    """
    taken = set(taken)
    stem = target_path.removesuffix(".md")
    for n in count(1):
        name = stem if n == 1 else f"{stem}-{n}"
        if f"{name}.md" not in taken and f"{name}.json" not in taken:
            return f"{name}.md"
    raise AssertionError("count() never ends")
