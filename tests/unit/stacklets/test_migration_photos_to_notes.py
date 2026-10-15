"""Migration: photos filed as bookmarks move to notes, once.

Photos were filed under `bookmarks/` until they became notes. The move
finds them in the local copy of the family memory, writes each under
`notes/` with its type saying so, and leaves everything that is not a
photo where it is: a saved link, a PDF. The entries are written by the
real renderer, so the move recognises them as the archivist writes them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

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
        body="", kind="bookmark", captured_at="2026-10-13", source_uri=source,
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


class _Forgejo:
    def __init__(self, files: dict[str, str]):
        self.files = {p: {"content": c, "sha": f"sha-{p}"} for p, c in files.items()}
        self.commits: list[list] = []

    def get_file(self, owner, repo, path):
        return self.files.get(path)

    def change_files(self, owner, repo, changes, *, message, author_name, author_email):
        self.commits.append(changes)
        for c in changes:
            if c.operation == "delete":
                self.files.pop(c.path)
            else:
                self.files[c.path] = {"content": c.content, "sha": "new"}


async def _move(tmp_path: Path, client: _Forgejo):
    for rel, info in client.files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(info["content"], encoding="utf-8")

    async def is_picture(mxc: str) -> bool:
        return is_picture_bytes(UPLOADS[mxc])

    return await migrate_photos_to_notes(
        client, "family", "memory", root=tmp_path, is_picture=is_picture,
        author_name="archivist-bot", author_email="archivist-bot@local",
    )


@pytest.mark.asyncio
async def test_photos_move_to_notes_and_nothing_else_does(tmp_path):
    client = _Forgejo(ENTRIES)
    moved = await _move(tmp_path, client)

    assert sorted(new for _, new in moved) == [
        "family/painting/notes/2026/10/glaze-dddddd.md",
        "family/painting/notes/2026/10/raku-aaaaaa.md",
    ]
    assert sorted(p for p in client.files if "/bookmarks/" in p) == [
        f"{DIR}/article-cccccc.md", f"{DIR}/form-bbbbbb.md", f"{DIR}/scan-eeeeee.md",
    ]
    assert len(client.commits) == 1


@pytest.mark.asyncio
async def test_a_moved_photo_says_it_is_a_note(tmp_path):
    client = _Forgejo(ENTRIES)
    await _move(tmp_path, client)

    note = client.files["family/painting/notes/2026/10/raku-aaaaaa.md"]["content"]
    assert parse_frontmatter(note)["type"] == "note"
    assert parse_frontmatter(note)["capture_id"] == "$raku-aaaaaa"
    assert "**Kind** note" in note
    assert "![[/media/2026/10/a.webp]]" in note


@pytest.mark.asyncio
async def test_with_nothing_left_to_move_nothing_is_written(tmp_path):
    client = _Forgejo({k: v for k, v in ENTRIES.items() if "raku" not in k and "glaze" not in k})
    assert await _move(tmp_path, client) == []
    assert client.commits == []


def test_pictures_are_told_from_other_files_by_their_first_bytes():
    assert is_picture_bytes(b"\x89PNG\r\n\x1a\n")
    assert is_picture_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    assert is_picture_bytes(b"\x00\x00\x00\x18ftypheic")
    assert not is_picture_bytes(b"%PDF-1.7")
    assert not is_picture_bytes(b"")
