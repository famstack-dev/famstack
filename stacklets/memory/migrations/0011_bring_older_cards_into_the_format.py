"""Bring the cards of an older instance into the format.

An instance older than 0.3.0-beta.2 holds cards in a format that migrations
0002 to 0010 read wrongly or skip, and 0.4 left its own traces on emails. A
card with a `type` is held to its schema, so the curator refuses a later write
of one in such a state: a reply folded into an email thread, an edit, a
reprocess. A card with no `type` gets no schema check, and stays outside the
format: no type, no id, and no pass of 0002 to 0008 reads it.

- A document had no `type` and no `kind`, so 0002 took it for a page written
  by hand and made it a note.
- A bookmark or a note said `kind: bookmark` and had no `type`, so 0002 to
  0008 skipped it.
- Those records say `added` for `timestamp` and `source_uri` for `resource`.
- An email from 0.4 carries the mail bot's tags as written (`Mailbox: x`,
  `Folder: INBOX`, `Sender: a@b`), which 0005 left for a person, and
  `capture_id` and `filed_by`, which an email card does not have.

What it does, each step reading what the one before it wrote: gives such a
card its type (a note with a Paperless id is the document it was: a document's
id, and its note id in `former_card_ids`, so a link to the note id still finds
it); renames the two fields; then the passes of 0002, 0003, 0004, 0006 and
0008, which now see these cards. On every record, of any age, `persons` keeps
declared people by id and `tags` follow the archivist's rule (`Moe's` becomes
`moe-s`). No model is called.

A card's text changes only where 0003 and 0008 change it. A name nobody goes
by leaves `persons`, and a tag with no letter leaves `tags`, each named in the
output. The commit's history keeps them. With a person file that cannot be
read, nothing can say that nobody goes by a name, so no card's `persons` changes
and the output names the file.

Left as it is and named in the output: a card the rules cannot read, such as
`kind: x` with no type, a blank `type`, or both `added` and `timestamp`. An
`ontology.toml` that cannot be read leaves every card's tags, also named. A
diary card is left alone: it is signed with a digest, and an edit from outside
the compile would make it the family's for good.

The steps build on each other, so they run on a temporary clone, and the one
write is the commit at the end, by the memory bot, through the curator. A
memory with nothing to repair gets no commit. A failed migration holds the
curator and the bots (`stack.migrations.until_migrated`), so nothing on a card
makes it fail. Besides git or the curator not answering, only two things raise:
no person files, as in 0003, and a commit the curator refuses, because a file
changed meanwhile. Nothing is written then, and the next `stack up memory` reads
again. Undo: revert the commit in family/memory.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..lib import (
    CardIds,
    CardLinks,
    _commit,
    _markdown_files,
    _parse_frontmatter,
    card_ids_for,
    card_kinds_for,
    declared_cards_for,
    get_ontology,
    memory_for_migration,
    note_sources_for,
    people_by_id_for,
    run_git,
)
from stack.cards import CARD_ID, FORMER_CARD_IDS, card_identity
from stack.cards.links import LINKED_RECORDS, About, without_person_tags
from stack.cards.sources import sidecar_path
from stack.frontmatter import with_field
from stack.people import load_people
from stack.tags import card_tags
from stack.vault import DEFAULT_SHARED_BUCKET

REPO = "family/memory"

# The old names of two fields of a record, with today's.
_RENAMED = {"added": "timestamp", "source_uri": "resource"}

# What a capture's `kind` said it was before it had a `type`.
_CAPTURE_KINDS = ("bookmark", "note")

# What an email card does not have (schemas/cards/email.json). Who filed an
# email is in the commit's author.
_NOT_ON_AN_EMAIL = ("capture_id", "filed_by")

# Fields of a record that hold a list; the rules cannot read anything else there.
_LIST_FIELDS = ("tags", "persons")

# A card's first line: its frontmatter opens.
_OPENING = "---\n"

# A clone of a family's memory can take longer than git's usual minute.
_CLONE_SECONDS = 600


def run(ctx):
    memory, vault = memory_for_migration(ctx)
    bucket = ctx.env.get("SHARED_BUCKET") or DEFAULT_SHARED_BUCKET
    media_dir = ctx.env.get("MEDIA_ARCHIVE_DIR", "")
    # As 0003 stops: with nobody declared, every name would read as unknown
    # and leave its card's `persons`.
    if not load_people(vault, bucket):
        raise RuntimeError(f"no person files under {bucket}/people/ to name people by")
    with tempfile.TemporaryDirectory(prefix="memory-older-cards-") as work:
        copy = _clone(vault, Path(work) / "memory")
        repair = repair_of(copy, shared_bucket=bucket,
                           media_root=Path(media_dir) if media_dir else None)
        # Said before the write, so a refused commit still shows what it found.
        for line in repair.said:
            ctx.step(f"Memory: {line}")
        # Each file is written on the version the passes read, never on the
        # one the curator's clone holds by now: an edit it pulled meanwhile
        # would pass as the base and be overwritten. So `_commit` hashes the
        # clone, back as it was before the passes.
        _restore(copy)
        commit = _commit(memory, copy, repair.changes, created=repair.created,
                         title=f"bring {len(repair.changes)} older file(s) into the format",
                         trailer=ctx.trailer)
    if commit:
        ctx.step(f"Memory: brought {len(repair.changes)} older file(s) into the format")
    return commit


# ── The repair ────────────────────────────────────────────────────────────

@dataclass
class Repair:
    """What the passes make of a memory.

    `changes` maps a path to the file's new text, `created` holds the paths
    among them that are new, `said` the lines for the admin.
    """

    changes: dict[str, str] = field(default_factory=dict)
    created: frozenset[str] = frozenset()
    said: list[str] = field(default_factory=list)


def repair_of(copy: Path, *, shared_bucket: str, media_root: Path | None) -> Repair:
    """Run every pass over the clone `copy`, which ends up changed, and return
    what differs from what it held before. A pass never removes a file.

    The passes run on every file, and the result leaves out what must stay as
    it is: a diary card, a card the rules cannot read, and a sidecar made for
    one. The order matters: a card has its type before anything asks for it,
    and names leave `persons` before 0003's pass, which then reports only
    links, so a name is reported once.

    A person file that cannot be read takes both people passes out: that
    person's id would read as a name nobody goes by, and leave every card of
    theirs.
    """
    before = _markdown_files(copy)
    unreadable = unreadable_cards(before)
    unread_people = _unreadable_person_files(copy, shared_bucket)
    people_passes: list[Callable[[Path], CardIds | CardLinks]] = [] if unread_people else [
        lambda at: record_persons_for(at, shared_bucket=shared_bucket),
        lambda at: people_by_id_for(at, shared_bucket=shared_bucket),
    ]
    passes: list[Callable[[Path], CardIds | CardLinks]] = [
        typed_cards_for,
        renamed_fields_for,
        card_ids_for,
        *people_passes,
        lambda at: declared_cards_for(at, shared_bucket=shared_bucket),
        record_tags_for,
        card_kinds_for,
        email_fields_for,
        lambda at: note_sources_for(at, media_root=media_root),
    ]
    written: dict[str, str] = {}
    left: list[str] = []
    for one_pass in passes:
        found = one_pass(copy)
        for path, text in found.changes.items():
            (copy / path).parent.mkdir(parents=True, exist_ok=True)
            (copy / path).write_text(text, encoding="utf-8")
        _stage(copy)
        written.update(found.changes)
        left += found.left

    stays = _staying_as_they_are(before, unreadable)
    changes = {path: text for path, text in written.items()
               if path not in stays and text != before.get(path)}
    said = [f"{problem}; no card's `persons` changed" for problem in unread_people]
    said += [f"{path}: {why}; left as it is" for path, why in sorted(unreadable.items())]
    said += [line for line in dict.fromkeys(left) if not _about_one_of(line, stays)]
    tracked = _tracked(copy)
    return Repair(changes, frozenset(path for path in changes if path not in tracked), said)


def unreadable_cards(texts: dict[str, str]) -> dict[str, str]:
    """Each card the rules cannot read, with the reason. Looking at one card
    only, so a card stays out of the repair whatever the passes make of it."""
    found: dict[str, str] = {}
    for path, text in sorted(texts.items()):
        fields = _parse_frontmatter(text)
        # The frontmatter parser reads `type:` with no value as an empty list.
        if isinstance(fields.get("type"), list):
            found[path] = "`type` is blank or a list"
            continue
        card_type = _type_of(fields)
        kind = str(fields.get("kind") or "").strip()
        if not card_type and kind and kind not in _CAPTURE_KINDS:
            found[path] = f"`kind: {kind}` and no type"
        if (card_type or kind) not in LINKED_RECORDS:
            continue
        for old, new in _RENAMED.items():
            if old in fields and new in fields:
                found[path] = f"both `{old}` and `{new}`"
        for name in _LIST_FIELDS:
            if fields.get(name) and not isinstance(fields[name], list):
                found[path] = f"`{name}` is not a list"
    return found


def _unreadable_person_files(copy: Path, shared_bucket: str) -> list[str]:
    """What `load_people` found wrong with the person files, one line each,
    with the file's path. Empty when each file reads."""
    problems: list[str] = []
    load_people(copy, shared_bucket, report=problems.append)
    return problems


def _type_of(fields: dict) -> str:
    """A card's `type` as text. A `type` the parser read as a list comes back
    as text too, so it can be looked up, and it names no card type."""
    return str(fields.get("type") or "").strip()


def _staying_as_they_are(before: dict[str, str], unreadable: dict[str, str]) -> set[str]:
    """The paths the repair must not write: each diary card, each card the
    rules cannot read, and the sidecar beside any of them."""
    diary = {path for path, text in before.items()
             if _parse_frontmatter(text).get("type") == "diary"}
    cards = diary | set(unreadable)
    return cards | {sidecar_path(path) for path in cards}


def _about_one_of(line: str, paths: set[str]) -> bool:
    """Whether a pass's line for the admin (`<path>: <what>`) is about one of `paths`."""
    return line.partition(": ")[0] in paths


# ── The passes of the old format ──────────────────────────────────────────
#
# Each reads the clone and returns what it changes, in the shape of the
# migrations' passes (`CardLinks`): the repair's alone, so not in `lib.py`.

def typed_cards_for(copy: Path) -> CardLinks:
    """Each capture with `kind` and no `type` gets that type, and a note with
    a Paperless id is the document it was (`_as_document`)."""
    found = CardLinks()
    for path, text in sorted(_markdown_files(copy).items()):
        fields = _parse_frontmatter(text)
        card_type = _type_of(fields)
        kind = str(fields.get("kind") or "").strip()
        if not card_type and kind in _CAPTURE_KINDS:
            found.changes[path] = f"{_OPENING}type: {kind}\n{text[len(_OPENING):]}"
        elif card_type == "note" and fields.get("paperless_id") is not None:
            found.changes[path] = _as_document(text, fields, path)
    return found


def _as_document(text: str, fields: dict, path: str) -> str:
    """The note `text` as a document: `type`, a document's id, and the note's
    id among its former ones. A card that holds a document's id already keeps
    it, and has no former id to list."""
    identity = card_identity(fields, "document", path)
    text = with_field(text, "type", "document")
    text = with_field(text, CARD_ID, identity[CARD_ID])
    return with_field(text, FORMER_CARD_IDS, identity.get(FORMER_CARD_IDS, []))


def renamed_fields_for(copy: Path) -> CardLinks:
    """Each record's `added` as `timestamp` and `source_uri` as `resource`,
    value and place kept."""
    found = CardLinks()
    for path, text in sorted(_markdown_files(copy).items()):
        fields = _parse_frontmatter(text)
        if _type_of(fields) not in LINKED_RECORDS:
            continue
        new = text
        for old, renamed in _RENAMED.items():
            if old in fields and renamed not in fields:
                head, sep, rest = new.partition("\n---\n")
                new = re.sub(rf"^{old}:", f"{renamed}:", head, count=1, flags=re.MULTILINE) + sep + rest
        if new != text:
            found.changes[path] = new
    return found


def record_persons_for(copy: Path, *, shared_bucket: str) -> CardLinks:
    """Each record's `persons` as the archivist writes it: the ids of declared
    people. A name nobody goes by leaves it and is listed in `left`."""
    people = load_people(copy, shared_bucket) or []
    found = CardLinks()
    for path, text in sorted(_markdown_files(copy).items()):
        fields = _parse_frontmatter(text)
        persons = fields.get("persons")
        if _type_of(fields) not in LINKED_RECORDS or not isinstance(persons, list):
            continue
        about = About.of([str(p) for p in persons], people)
        if about.ids != persons:
            found.changes[path] = with_field(text, "persons", about.ids)
            found.left += [f"{path}: {name!r} leaves `persons`, nobody goes by it"
                           for name in about.unknown]
    return found


def record_tags_for(copy: Path) -> CardLinks:
    """Each record's tags as the archivist writes them (`stack.tags.card_tags`).
    A tag with no letter leaves them and is listed in `left`, and a `Person:`
    tag goes, as in 0003. With an ontology that cannot be read no tag can be
    read, so all stay, and `left` says why."""
    found = CardLinks()
    try:
        ontology = get_ontology(copy)
    except Exception as e:  # noqa: BLE001 - a hand edit can break it in many ways, none may hold the bots
        found.left.append(f"ontology.toml cannot be read ({e!r}), so no card's tags were changed")
        return found
    for path, text in sorted(_markdown_files(copy).items()):
        fields = _parse_frontmatter(text)
        tags = fields.get("tags")
        if _type_of(fields) not in LINKED_RECORDS or not isinstance(tags, list):
            continue
        kept = without_person_tags([str(t) for t in tags])
        written = card_tags(kept, ontology)
        if written != tags:
            found.changes[path] = with_field(text, "tags", written)
            found.left += [f"{path}: tag {tag!r} leaves `tags`, it holds no letter"
                           for tag in kept if not card_tags([tag], ontology)]
    return found


def email_fields_for(copy: Path) -> CardLinks:
    """Each email without the fields an email card does not have."""
    found = CardLinks()
    for path, text in sorted(_markdown_files(copy).items()):
        fields = _parse_frontmatter(text)
        if fields.get("type") != "email":
            continue
        new = text
        for name in _NOT_ON_AN_EMAIL:
            if name in fields:
                new = with_field(new, name, "")  # an empty value removes the field
        if new != text:
            found.changes[path] = new
    return found


# ── The clone ─────────────────────────────────────────────────────────────

def _clone(vault: Path, at: Path) -> Path:
    _git(vault, "clone", "-q", "--no-hardlinks", ".", str(at), timeout=_CLONE_SECONDS)
    return at


def _stage(copy: Path) -> None:
    """Add the clone's new files to its index, because the passes find the
    files through `git ls-files`. The clone gets no commit: its HEAD stays
    the commit the passes started from."""
    _git(copy, "add", "-A")


def _restore(copy: Path) -> None:
    """Put the clone's files back as they were at its HEAD."""
    _git(copy, "reset", "-q", "--hard", "HEAD")


def _tracked(copy: Path) -> set[str]:
    """Every path in the commit the passes started from."""
    return set(_git(copy, "ls-tree", "-r", "-z", "--name-only", "HEAD").split("\0"))


def _git(repo: Path, *args: str, timeout: int = 60) -> str:
    rc, out, err = run_git(repo, *args, timeout=timeout)
    if rc != 0:
        raise OSError(f"git {args[0]} failed in {repo}: {err}")
    return out
