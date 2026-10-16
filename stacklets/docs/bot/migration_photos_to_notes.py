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
from pathlib import Path
from typing import Awaitable, Callable

from loguru import logger

from stack.forgejo import FileChange, ForgejoClient
from stack.frontmatter import FrontmatterError
from stack.frontmatter import dump as dump_frontmatter
from stack.frontmatter import parse as parse_frontmatter

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


def as_note(text: str) -> str:
    """The entry with `type: note` and its Kind line saying so."""
    fm = parse_frontmatter(text)
    fm["type"] = "note"
    body = text[text.index("\n---\n", 4) + len("\n---\n"):]
    return "---\n" + dump_frontmatter(fm) + "\n---\n" + _KIND.sub(r"\1note", body, count=1)


def note_path(path: str) -> str:
    """`<bucket>/.../bookmarks/<yyyy>/<mm>/<file>` under `notes/` instead."""
    return path.replace("/bookmarks/", "/notes/", 1)


async def migrate_photos_to_notes(
    client: ForgejoClient, owner: str, repo: str, *, root: Path,
    is_picture: Callable[[str], Awaitable[bool]],
    author_name: str, author_email: str, trailer: str = "",
) -> tuple[list[tuple[str, str]], str | None]:
    """Move every photo bookmark to notes.

    Returns the `(old, new)` paths and the sha of the commit that moved
    them, None when there was nothing to move. `root` is the local copy
    of the vault, which says what to look at; each file is read from
    Forgejo before it is changed, so the commit is refused rather than
    overwriting an edit made since. `is_picture` answers for an `mxc://`
    address an entry alone cannot place. `trailer` ends the commit
    message.
    """
    async def read(path: str) -> dict | None:
        return await asyncio.to_thread(client.get_file, owner, repo, path)

    changes: list[FileChange] = []
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
        changes.append(FileChange("create", new, as_note(current.get("content") or "")))
        changes.append(FileChange("delete", rel, sha=current.get("sha") or ""))
        moved.append((rel, new))

    if not changes:
        return moved, None
    message = "notes: file the photos filed as bookmarks as notes"
    response = await asyncio.to_thread(
        client.change_files, owner, repo, changes,
        message=f"{message}\n\n{trailer}" if trailer else message,
        author_name=author_name, author_email=author_email,
    )
    logger.info("[migration] moved {} photo(s) to notes", len(moved))
    return moved, ((response or {}).get("commit") or {}).get("sha")
