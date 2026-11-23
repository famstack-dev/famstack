"""Writing a capture's card into the memory: one curator commit, then the after-steps.

    filed = await write_capture(memory, setting, after, via="archivist-bot",
                                entity="marge", kind="note", classification=reading, ...)
    filed.path, filed.card_id      # ('marge/notes/2026/11/grill-charcoal-0c401e.md', 'note-…')
    # or NotFiled('unreachable'): the curator was down, nothing was written

The memory has one writer, its curator (ADR-019, `stack.memory`). A capture
is written there in one commit: a note's card and its sidecar together, so
neither changes without the other (ADR-018). Each file is written on a
version the caller names (`commit_card`'s `base` and `sidecar_base`): ""
for a file that must not exist yet, else the version the caller read. A
file that changed since that version makes the curator refuse the commit
(`changed`), and nothing is overwritten.

`write_capture` takes the versions from its caller too (`base`,
`sidecar_base`). A caller that names none writes on the version read at
the card's path at write time.

After the commit the after-steps run (`AfterSteps`): the topic's card is
declared, a note's to-dos are folded into its topic's list. The intake's
writer fills them (the archivist's mirror); this module knows neither.

The path of a capture is `stack.filing.cards.capture_filepath`. A note on
its first filing is found by the message it was filed from
(`MemoryAPI.filed_from`, `stack.filing.identity`), right before the write,
and updated where it is; else it is written at the first free name of its
path, on an empty base, and a refusal because a file appeared there
meanwhile looks it up again and tries the next name. A bookmark already
filed under another title is found by the hash of its link
(`lookup_capture_path`) and updated where it is. Either way a new title
never moves a card.

Failures are logged and returned, never raised: a capture is best-effort,
and the intake tells the person what was not written.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import NamedTuple, Protocol

from loguru import logger

from ..cards.fields import FieldOp
from ..cards.sources import Source, sidecar_path
from ..memory import CuratorDown, File, MemoryAPI, Refused, put, why_not_written
from .cards import CardContext, capture_entry, capture_filepath
from .identity import NAMES_TRIED, free_name, notes_of

__all__ = ["AfterSteps", "Filed", "NotFiled", "commit_card", "lookup_capture_path", "not_written",
           "write_capture"]


class Filed(NamedTuple):
    """Where a capture landed in the memory and the card id it carries there.

        filed = await write_capture(...)
        filed.path, filed.card_id   # ('marge/notes/2026/10/tent-3a338e.md', 'note-7f3a9c21e4')
    """

    path: str
    card_id: str
    # What a correction changed on the card, read off it before and after;
    # None when the writer does not say (every write but an email's correction).
    changes: "tuple | None" = None
    # A capture's people (ids, then names its About line shows without one) and
    # tags; also its title after an email's correction; else None.
    card: dict | None = None


class NotFiled(NamedTuple):
    """A capture the memory did not write, and why, as a code.

        filed = await write_capture(...)
        if isinstance(filed, NotFiled):
            filed.reason   # 'unreachable'

    The code is one of `stack.memory.NOT_WRITTEN`, `stack.memory.UNKNOWN`,
    or the writer's own: `card_missing` (the card is gone) and `unreadable`
    (its frontmatter does not parse). The intake words it for the person,
    so a refused write is never answered "Captured" or "Reclassified". The
    curator's own words go to the log.
    """

    reason: str


class AfterSteps(Protocol):
    """What runs after a capture's commit, filled by the intake's writer.

        class Mirror:
            async def after_filing(self, path, classification, *, by, previous_path): ...

    ``path`` is where the card is now, ``previous_path`` where it was
    before ("" for a first filing), ``by`` who the commit was by.
    """

    async def after_filing(self, path: str, classification: dict, *, by: str,
                           previous_path: str) -> None: ...


async def write_capture(
    memory: MemoryAPI, setting: CardContext, after: AfterSteps, *,
    via: str,
    entity: str,
    kind: str,
    source_uri: str | None,
    title_hint: str | None,
    body_text: str,
    classification: dict,
    captured_at: str,
    model: str | None,
    tags: list[str] | None = None,
    existing_path: str | None = None,
    capture_id: str | None = None,
    submitter: str | None = None,
    kept_media: dict | None = None,
    caption: str = "",
    replies: "list[tuple[str, str]] | tuple" = (),
    clean: str | None = None,
    sources: "Sequence[Source]" = (),
    made: dict | None = None,
    ops: "Sequence[FieldOp]" = (),
    changed_by: str | None = None,
    base: str | None = None,
    sidecar_base: str | None = None,
) -> Filed | NotFiled:
    """Create or update a capture's card (a note or a bookmark) in the memory.

    Routes the card under `<entity>/<kind>s/...` (`capture_filepath`), with
    the link, else ``body_text``, as its hash. ``existing_path`` is a
    refile's: the card is rewritten where it is, whatever its new title,
    on ``base`` and a note's sidecar on ``sidecar_base`` (`commit_card`):
    the version the caller read, None for the version read now.

    Without it, a note is the card filed from its message ``capture_id``
    (`MemoryAPI.filed_from`), updated on the version read now, else a new
    card at the first free name of its path (`identity.free_name`), on an
    empty base; a refusal because a file appeared there looks it up again
    and tries the next name. A file at a note's path is never updated for
    being there. A bookmark of the same hash in the folder is updated where
    it is (`lookup_capture_path`), on ``base`` as above.

    ``via`` is the account that commits (the committer, ADR-015). The
    commit is by ``changed_by`` (a refile's corrector), else ``submitter``
    (the filer), else ``via``. The other arguments are the card's
    (`stack.filing.cards.capture_entry`).

    Returns where the card landed and its id, or `NotFiled` with the
    reason when the curator cannot be reached or refused the write.

        filed = await write_capture(memory, setting, mirror, via="archivist-bot",
                                    entity="marge", kind="bookmark",
                                    source_uri="https://krusty.test/tent", title_hint=None,
                                    body_text="", classification={"title": "Tent"},
                                    captured_at="2026-11-22", model=None)
        filed.path    # 'marge/bookmarks/2026/11/tent-5d1e2a.md'
    """
    resolved_title = classification.get("title") or title_hint
    title = resolved_title or "Capture"
    target_path = capture_filepath(entity, kind, captured_at, title if resolved_title else None,
                                   source_uri or body_text)
    first_note = kind == "note" and existing_path is None
    for _ in range(NAMES_TRIED if first_note else 1):
        try:
            place = await (_new_note_place(memory, target_path, entity=entity, capture_id=capture_id)
                           if first_note else
                           _capture_place(memory, target_path, kind=kind, existing_path=existing_path))
        except (Refused, CuratorDown) as e:
            logger.warning("[filing] Capture not filed for {}: {}", source_uri or "(paste)", e)
            return not_written(e)
        # A new note is written on an empty base: a file that appeared at
        # its name is never overwritten. A note found by its message is
        # written on the versions just read.
        if first_note:
            card_base, side_base = _version(None, place.existing), _version(None, place.side)
        else:
            card_base, side_base = _version(base, place.existing), _version(sidecar_base, place.side)
        written = await _write_at(
            memory, setting, after, place, via=via, kind=kind, source_uri=source_uri,
            title_hint=title_hint, title=title, classification=classification,
            captured_at=captured_at, model=model, tags=tags, capture_id=capture_id,
            submitter=submitter, kept_media=kept_media, caption=caption, replies=replies,
            clean=clean, sources=sources, made=made, ops=ops, changed_by=changed_by,
            base=card_base, sidecar_base=side_base)
        # A file that appeared at a new note's name meanwhile: the message
        # is looked up again, then the next free name is tried.
        if not (first_note and place.existing is None and written == NotFiled("changed")):
            return written
    return NotFiled("changed")


class _Place(NamedTuple):
    """Where a capture is written: its path, the card and the sidecar there
    now (None when there is none), and where it was before ("" for none)."""

    path: str
    existing: "File | None"
    side: "File | None"
    previous: str


async def _capture_place(memory: MemoryAPI, target_path: str, *, kind: str,
                         existing_path: str | None) -> _Place:
    """A bookmark's place, or a correction's: the path it is filed under.

    A capture keeps the path it was first filed under. Topic pages, list
    items and the link in the chat point at it, so a new title or a
    correction from someone else in the room changes what the entry says,
    never where it is. A bookmark is found by the hash of its link
    (`lookup_capture_path`), so a re-pasted link updates its entry.
    """
    if existing_path is None:
        existing_path = await lookup_capture_path(memory, target_path)
    path = existing_path or target_path
    existing = await asyncio.to_thread(memory.file, path)
    side = await asyncio.to_thread(memory.file, sidecar_path(path)) if kind == "note" else None
    return _Place(path, existing, side, existing_path or "")


async def _new_note_place(memory: MemoryAPI, target_path: str, *, entity: str,
                          capture_id: str | None) -> _Place:
    """A note's place on its first filing: the card filed from its message,
    else the first free name at its path (`identity.free_name`).

    Only the lookup by its message (`MemoryAPI.filed_from`) makes a note's
    first filing an update; a file at its path is never read to be updated,
    so two notes with the same link and title stay two notes.
    """
    found = await asyncio.to_thread(memory.filed_from, capture_id, under=notes_of(entity)) \
        if capture_id else None
    if found is not None:
        side = await asyncio.to_thread(memory.file, sidecar_path(found.path))
        return _Place(found.path, found, side, found.path)
    taken = await asyncio.to_thread(memory.paths, target_path.rpartition("/")[0])
    return _Place(free_name(target_path, taken), None, None, "")


async def _write_at(
    memory: MemoryAPI, setting: CardContext, after: AfterSteps, place: _Place, *,
    via: str, kind: str, source_uri: str | None, title_hint: str | None, title: str,
    classification: dict, captured_at: str, model: str | None, tags: list[str] | None,
    capture_id: str | None, submitter: str | None, kept_media: dict | None, caption: str,
    replies: "list[tuple[str, str]] | tuple", clean: str | None, sources: "Sequence[Source]",
    made: dict | None, ops: "Sequence[FieldOp]", changed_by: str | None,
    base: str, sidecar_base: str,
) -> Filed | NotFiled:
    """Write the capture's card at `place`, on `base` and `sidecar_base`,
    then run the after-steps; where it landed, or `NotFiled`."""
    target_path, existing = place.path, place.existing
    entry = capture_entry(
        setting, target_path=target_path, existing=existing.text if existing else None,
        kind=kind, source_uri=source_uri, title_hint=title_hint,
        classification=classification, captured_at=captured_at, model=model, tags=tags,
        capture_id=capture_id, submitter=submitter, kept_media=kept_media,
        caption=caption, replies=replies, clean=clean, sources=sources, made=made, ops=ops,
    )
    for line in entry.left_out:
        logger.warning("[filing] {}: a field edit no longer fits, left out: {}", target_path, line)

    # The filer stays the card's `filed_by` (`submitter`). The commit is
    # by whoever made this change: on a refile from a reply in the chat
    # that is the person who replied (`changed_by`), so `git log
    # --author` and the dashboard name them, and the subject says it was
    # a reply. Committer stays `via` (ADR-015).
    by = changed_by or submitter or via
    verb = ("reply" if changed_by else "update") if existing else "capture"
    refused = await commit_card(
        memory, target_path, entry.content, entry.sidecar if kind == "note" else None,
        base=base, sidecar_base=sidecar_base,
        by=by, via=via, verb=verb, title=title, body=classification.get("summary") or "",
        trailers={"Source": source_uri or "(paste)", **({"Model": model} if model else {})},
        label=source_uri or "(paste)")
    if refused is not None:
        return refused

    await after.after_filing(target_path, classification, by=by, previous_path=place.previous)
    fm = entry.frontmatter
    return Filed(target_path, str(fm.get("card_id") or ""), card={
        "persons": [*(fm.get("persons") or []), *entry.about.unknown], "tags": list(fm.get("tags") or [])})


async def commit_card(
    memory: MemoryAPI, path: str, content: str, sidecar: str | None, *,
    base: str, sidecar_base: str, by: str, via: str, verb: str, title: str,
    body: str, trailers: dict[str, str], label: str,
) -> NotFiled | None:
    """Write a card, and a note's ``sidecar`` beside it, in one curator commit.

    ``base`` and ``sidecar_base`` are the versions the caller writes on: ""
    for a file that must not exist yet, else the version it read. The
    curator refuses the commit when a file changed since (`changed`), so
    nothing is overwritten. ``label`` names the capture in the log.

    Returns None when the commit was written, else `NotFiled` with why.

        refused = await commit_card(memory, "marge/notes/2026/11/grill-0c401e.md", card, side,
                                    base="", sidecar_base="", by="@marge:simpson",
                                    via="archivist-bot", verb="capture", title="Grill charcoal",
                                    body="", trailers={"Source": "(paste)"}, label="(paste)")
        refused    # None: card and sidecar are in one new commit
    """
    changes = [put(path, content, base=base)]
    if sidecar is not None:
        changes.append(put(sidecar_path(path), sidecar, base=sidecar_base))
    try:
        await asyncio.to_thread(memory.commit, changes, by=by, via=via, kind=verb, title=title,
                                body=body, trailers=trailers)
    except Refused as e:
        if e.reason == "changed":
            logger.warning("[filing] Capture not written, nothing overwritten: {}", e)
        else:
            logger.warning("[filing] Capture publish failed for {}: {}", label, e)
        return not_written(e)
    except CuratorDown as e:
        logger.warning("[filing] Capture publish failed for {}: {}", label, e)
        return not_written(e)
    logger.info("[filing] {} → {}", verb, path)
    return None


async def lookup_capture_path(memory: MemoryAPI, target_path: str) -> str | None:
    """An existing capture with `target_path`'s identity, ignoring its title.

    A capture's filename is `<title-slug>-<hash>.md`; the hash is stable
    per link or words, the slug is not. So the same link re-pasted with
    different framing, or content re-classified to a different title, would
    otherwise fork a near-duplicate. Matches on the `<entity>/<kind>s/`
    folder and the `-<hash>.md` suffix: returns the exact path when it
    exists, else a same-hash file under a different slug (which the caller
    then updates where it is), else None.

        await lookup_capture_path(memory, "marge/notes/2026/11/new-title-0c401e.md")
        # 'marge/notes/2026/11/grill-charcoal-0c401e.md'
    """
    parts = target_path.split("/")
    if len(parts) < 2:
        return None
    folder = f"{parts[0]}/{parts[1]}"               # <entity>/<kind>s
    suffix = "-" + target_path.rsplit("-", 1)[-1]   # -<hash>.md
    match = None
    for path in await asyncio.to_thread(memory.paths, folder):
        if path.endswith(suffix):
            if path == target_path:
                return path        # exact identity + title → update in place
            match = path           # same identity, different title
    return match


def _version(named: str | None, read) -> str:
    """The version a file is written on: the one the caller named, else the
    one read now ("" when there is no file)."""
    if named is not None:
        return named
    return read.version if read else ""


def not_written(e: Refused | CuratorDown) -> NotFiled:
    """Why the curator did not write a change, as its code. The caller has
    logged the curator's own words.

        not_written(CuratorDown("connection refused"))   # NotFiled('unreachable')
    """
    return NotFiled(why_not_written(e))
