"""Move the photos filed as bookmarks to notes: migration docs/0001.

A photo posted outside the documents room used to be filed under
`bookmarks/`, next to saved links. It is the family's own record and is
filed under `notes/` now (`capture_pipeline`). This moves the ones filed
before, in one commit. Nothing else needs to follow: chat links and
corrections find a capture by its `capture_id`, the wiki's pages are
rebuilt from the new paths, and a bookmark never put items on a list.

`stacklets/docs/migrations/0001_photos_to_notes.py` runs it once per
instance through the `migrate-photos-to-notes` command in the bot
runner (`cli/migrate_photos_to_notes.py`). Kept until 1.0, like every
migration.

Which bookmarks are photos: since 0.4.0-rc.1 an entry shows its picture
(`![[/media/...]]`), and a PDF is only named (`**File**`). Entries filed
before that carry only the `mxc://` address of the upload, so the caller
answers for those by looking at the file itself (`is_picture_bytes`).
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from typing import Awaitable, Callable

from loguru import logger

from stack.cards import card_identity
from stack.cards.sources import sidecar_path
from stack.frontmatter import FrontmatterError
from stack.frontmatter import dump as dump_frontmatter
from stack.frontmatter import parse as parse_frontmatter

from memory_client import ARCHIVIST, MemoryClient, put, remove

# Sibling stacklets resolve through `stacklets/` on sys.path: in the
# bot-runner container that directory is the read-only mount, locally it
# is the source tree. The path goes on here, before the import that needs
# it, rather than relying on who loads whom (as in `pipeline.py`).
_STACKLETS_DIR = Path(__file__).resolve().parents[2]
if str(_STACKLETS_DIR) not in sys.path:
    sys.path.insert(0, str(_STACKLETS_DIR))
from stacklets.memory.note_card import legacy_note  # noqa: E402

# As `vault_entry.render_capture` writes them.
_SHOWN = re.compile(r"^!\[\[/media/[^\]]+\]\]", re.M)
_NAMED = re.compile(r"^> \*\*File\*\* ", re.M)
_KIND = re.compile(r"^(> .*\*\*Kind\*\* )bookmark", re.M)

PICTURE = "picture"
UNKNOWN = "unknown"

# The first bytes of the picture formats a phone or a camera sends.
_PICTURE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8")
_HEIF_BRANDS = (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"avif")


def is_picture_bytes(data: bytes) -> bool:
    """Whether `data` is a JPEG, PNG, GIF, WebP or HEIC/AVIF picture."""
    if data.startswith(_PICTURE_MAGIC):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS


def verdict(text: str) -> str | None:
    """`PICTURE`, `UNKNOWN` when only the file can tell, or None."""
    try:
        fm = parse_frontmatter(text)
    except FrontmatterError:
        return None
    if fm.get("type") != "bookmark" or not str(fm.get("resource") or "").startswith("mxc://"):
        return None
    if _SHOWN.search(text):
        return PICTURE
    if _NAMED.search(text):
        return None
    return UNKNOWN


def as_note(text: str, path: str, *,
            media_sha: Callable[[str], str] = lambda link: "") -> tuple[str, str]:
    """The entry at `path` as a note: its card and its sidecar (ADR-018).

    A card that changes its type is a new card (ADR-016): it gets a note's
    card id and lists the bookmark's in `former_card_ids`, in the same
    commit, so a link to the bookmark still finds it. The photo and its
    caption become the note's source, as migration memory/0008 converts
    every older note (`memory.note_card.legacy_note`); `media_sha` answers
    for the hash of the archived photo.
    """
    fm = parse_frontmatter(text)
    identity = card_identity(fm, "note", path)
    rest = {k: v for k, v in fm.items() if k not in ("type", *identity)}
    body = text[text.index("\n---\n", 4) + len("\n---\n"):]
    note = ("---\n" + dump_frontmatter({"type": "note", **identity, **rest}) + "\n---\n"
            + _KIND.sub(r"\1note", body, count=1))
    converted = legacy_note(note, path=path, media_sha=media_sha)
    return converted.card, converted.sidecar


def note_path(path: str) -> str:
    """`<bucket>/.../bookmarks/<yyyy>/<mm>/<file>` under `notes/` instead."""
    return path.replace("/bookmarks/", "/notes/", 1)


async def migrate_photos_to_notes(
    memory: MemoryClient, *, root: Path,
    is_picture: Callable[[str], Awaitable[bool]], trailer: str = "",
    media_sha: Callable[[str], str] = lambda link: "",
) -> tuple[list[tuple[str, str]], str | None]:
    """Move every photo bookmark to notes.

    Returns the `(old, new)` paths and the sha of the commit that moved
    them, None when there was nothing to move. `root` is the local copy
    of the vault, which says what to look at; each file is read from the
    curator before it is changed and written on the version it was read
    at, so the commit is refused rather than overwriting an edit made
    since. One commit by the archivist, through the curator. `is_picture`
    answers for an `mxc://` address an entry alone cannot place,
    `media_sha` for the hash of an archived photo (`as_note`). `trailer`
    (`Migration: docs/0001`) ends the commit message.
    """
    async def read(path: str):
        return await asyncio.to_thread(memory.file, path)

    changes: list[dict] = []
    moved: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        if "/bookmarks/" not in rel or ".git" in path.parts:
            continue
        local = path.read_text(encoding="utf-8", errors="replace")
        found = verdict(local)
        if found is None:
            continue
        if found == UNKNOWN and not await is_picture(str(parse_frontmatter(local)["resource"])):
            continue
        current, new = await read(rel), note_path(rel)
        if not current or await read(new):
            continue
        card, sidecar = as_note(current.text, new, media_sha=media_sha)
        changes += [put(new, card, base=""), put(sidecar_path(new), sidecar, base=""),
                    remove(rel, base=current.version)]
        moved.append((rel, new))

    if not changes:
        return moved, None
    key, _, value = trailer.partition(": ")
    written = await asyncio.to_thread(
        memory.commit, changes, by=ARCHIVIST, kind="notes",
        title="file the photos filed as bookmarks as notes",
        trailers={key: value} if value else {})
    logger.info("[migration] moved {} photo(s) to notes", len(moved))
    return moved, written.commit or None
