"""Migration: photos filed as bookmarks move to notes, once.

Photos were filed under `bookmarks/` until they became notes. The move
finds them in the curator's copy of the family memory, writes each under
`notes/` with its type saying so, through the curator, and leaves everything that is not a
photo where it is: a saved link, a PDF. The entries are written by the
real renderer, so the move recognises them as the archivist writes them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))
sys.path.insert(0, str(Path(__file__).parent))

from memory_client import MemoryClient  # noqa: E402
from memory_git import git, running_memory  # noqa: E402
from migration_photos_to_notes import is_picture_bytes, migrate_photos_to_notes  # noqa: E402
from stack.frontmatter import parse as parse_frontmatter  # noqa: E402
from vault_entry import capture_frontmatter, render_capture  # noqa: E402

DIR = "family/painting/bookmarks/2026/10"


def _entry(path: str, *, source: str, kept: dict | None = None) -> str:
    return render_capture(
        frontmatter=capture_frontmatter(
            title="X", captured_at="2026-10-13", kind="bookmark", source_uri=source,
            persons=[], tags=[], model=None, capture_id=f"${Path(path).stem}",
        ),
        kind="bookmark", captured_at="2026-10-13", source_uri=source,
        persons=[], from_path=path, shared_bucket="family", summary="s", facts=[],
        kept_media=kept,
    )


ENTRIES = {
    # Filed since 0.4.0-rc.1: the picture is on the page.
    f"{DIR}/raku-aaaaaa.md": _entry(f"{DIR}/raku-aaaaaa.md", source="mxc://h/1", kept={
        "name": "IMG_1.jpg", "original": "/media/2026/10/a.jpg", "embed": "/media/2026/10/a.webp"}),
    # A PDF: named, never shown.
    f"{DIR}/form-bbbbbb.md": _entry(f"{DIR}/form-bbbbbb.md", source="mxc://h/2", kept={
        "name": "form.pdf", "original": "/media/2026/10/b.pdf", "embed": ""}),
    # A saved link.
    f"{DIR}/article-cccccc.md": _entry(f"{DIR}/article-cccccc.md", source="https://example.org/a"),
    # Filed before the media archive: only the upload's address.
    f"{DIR}/glaze-dddddd.md": _entry(f"{DIR}/glaze-dddddd.md", source="mxc://h/old-photo"),
    f"{DIR}/scan-eeeeee.md": _entry(f"{DIR}/scan-eeeeee.md", source="mxc://h/old-pdf"),
}
UPLOADS = {"mxc://h/old-photo": b"\xff\xd8\xff\xe0 jpeg", "mxc://h/old-pdf": b"%PDF-1.7"}


@pytest.fixture
def memory(tmp_path, request):
    """The family memory holding `ENTRIES`, or the entries a test names
    (`indirect`), with the curator's API in front of it."""
    with running_memory(tmp_path / "data", getattr(request, "param", None) or ENTRIES,
                        seeded_by="archivist-bot", seeded_as="capture: the photos") as memory:
        yield memory


async def _move(memory):
    """The move as the bot runner runs it: the curator's clone says what to
    look at, the curator reads and writes."""
    async def is_picture(mxc: str) -> bool:
        return is_picture_bytes(UPLOADS[mxc])

    return await migrate_photos_to_notes(
        MemoryClient.from_environ(memory.environ), root=memory.vault, is_picture=is_picture,
        trailer="Migration: docs/0001")


def _in_forgejo(memory) -> list[str]:
    return git(memory.forgejo.repo, "ls-tree", "-r", "--name-only", "main").splitlines()


async def test_photos_move_to_notes_and_nothing_else_does(memory):
    moved, commit = await _move(memory)

    assert sorted(new for _, new in moved) == [
        "family/painting/notes/2026/10/glaze-dddddd.md",
        "family/painting/notes/2026/10/raku-aaaaaa.md",
    ]
    assert sorted(p for p in _in_forgejo(memory) if "/bookmarks/" in p) == [
        f"{DIR}/article-cccccc.md", f"{DIR}/form-bbbbbb.md", f"{DIR}/scan-eeeeee.md",
    ]
    assert memory.forgejo.new_commits() == [
        "archivist-bot <archivist-bot@local> | archivist-bot <archivist-bot@local> | "
        "notes: file the photos filed as bookmarks as notes"]
    assert memory.forgejo.message().endswith("Migration: docs/0001")
    assert commit == git(memory.forgejo.repo, "rev-parse", "main")


async def test_a_moved_photo_says_it_is_a_note(memory):
    await _move(memory)

    note = memory.text("family/painting/notes/2026/10/raku-aaaaaa.md")
    assert parse_frontmatter(note)["type"] == "note"
    assert "## Sources\n\n> ![[/media/2026/10/a.webp]]" in note


async def test_a_moved_photo_is_a_note_of_today_with_its_sidecar(memory):
    """A note is its card and its sidecar (ADR-018), so a photo moved
    after migration memory/0008 ran is not left in the older format."""
    await _move(memory)

    sidecar = json.loads(memory.text("family/painting/notes/2026/10/raku-aaaaaa.json"))
    [original] = sidecar["sources"]
    assert (original["role"], original["kind"], original["id"]) == ("original", "image", "$raku-aaaaaa")
    assert sidecar["card_id"] == parse_frontmatter(
        memory.text("family/painting/notes/2026/10/raku-aaaaaa.md"))["card_id"]


@pytest.mark.parametrize("memory", [{k: v for k, v in ENTRIES.items()
                                     if "raku" not in k and "glaze" not in k}], indirect=True)
async def test_with_nothing_left_to_move_nothing_is_written(memory):
    assert await _move(memory) == ([], None)
    assert memory.forgejo.new_commits() == []


def test_pictures_are_told_from_other_files_by_their_first_bytes():
    assert is_picture_bytes(b"\x89PNG\r\n\x1a\n")
    assert is_picture_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    assert is_picture_bytes(b"\x00\x00\x00\x18ftypheic")
    assert not is_picture_bytes(b"%PDF-1.7")
    assert not is_picture_bytes(b"")
