"""The media archive — what `stack.media` promises about kept originals.

The archive exists so that the page describing an artifact is not the
only thing left of it. Everything here is about that promise holding
over time: a file written once is never rewritten, the link a page gets
back keeps working from any depth, an identifier that arrives from the
outside cannot address anything but the archive, and a repository that
holds the tree never commits it.

Ground truth for the ignore rule is git itself: the test runs `git
check-ignore` against a real repository rather than asserting on the
text of the file, so the assertion is about the effect the rule has and
not about how it was spelled.
"""

from __future__ import annotations

import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from stack import media

MARCH = date(2026, 3, 14)


@pytest.fixture
def archive(tmp_path) -> Path:
    return media.archive_root(tmp_path)


# ── Keeping an original ──────────────────────────────────────────────────

class TestStore:
    """An identifier names one artifact, permanently."""

    def test_the_link_is_rooted_at_the_site_not_at_the_archive(self, archive):
        """Pages sit at every depth and are read for years. A link from
        the site root is the one form that resolves the same in the
        rendered wiki and in a local vault client."""
        link = media.store(archive, "$abc", b"bytes", ext="png", when=MARCH)

        assert link == "/media/2026/03/abc.png"

    def test_the_bytes_land_where_the_link_says(self, archive):
        link = media.store(archive, "$abc", b"bytes", ext="png", when=MARCH)

        kept = archive.parent / link.lstrip("/")
        assert kept.read_bytes() == b"bytes"

    def test_an_artifact_already_kept_is_not_written_again(self, archive):
        """Re-running a compile must not touch files it kept on an
        earlier pass: the identifier is the promise that the bytes are
        the same ones, and a rewrite is churn the wiki would rebuild
        over."""
        first = media.store(archive, "$abc", b"original", ext="png", when=MARCH)
        kept = archive.parent / first.lstrip("/")
        before = kept.stat().st_mtime_ns

        again = media.store(archive, "$abc", b"different", ext="png", when=MARCH)

        assert again == first
        assert kept.read_bytes() == b"original"
        assert kept.stat().st_mtime_ns == before

    def test_the_month_comes_from_the_date_the_artifact_belongs_to(self, archive):
        """Not from the clock: an artifact filed today can belong to a
        month years back, and the archive follows the page."""
        link = media.store(
            archive, "$old", b"x", ext="jpg",
            when=datetime(2019, 11, 2, 23, 30, tzinfo=timezone.utc),
        )

        assert link == "/media/2019/11/old.jpg"

    def test_a_failed_write_reports_itself_rather_than_raising(self, tmp_path):
        """A caller that cannot archive still has the link it had. The
        page is worth more than the copy, so a full disk or a read-only
        mount costs the copy and nothing else."""
        blocked = tmp_path / "file-where-a-directory-should-be"
        blocked.write_text("not a directory")

        assert media.store(blocked, "$abc", b"x", ext="png", when=MARCH) == ""

    def test_a_partial_write_is_never_visible_under_the_final_name(self, archive):
        """The site serves this tree while it is being written, so the
        file appears whole or not at all. Its arrival is a rename, and
        nothing is left behind next to it."""
        media.store(archive, "$abc", b"x" * 4096, ext="png", when=MARCH)

        month = archive / "2026" / "03"
        assert [p.name for p in month.iterdir()] == ["abc.png"]


# ── Identifiers from the outside ─────────────────────────────────────────

class TestIdentifiersAreNotPaths:
    """Identifiers arrive on events from outside the stack. None of them
    may address anything but the archive."""

    @pytest.mark.parametrize("crafted", [
        "../../../etc/passwd",
        "/etc/passwd",
        "..",
        "../../secrets",
        "a/../../b",
        "$evil/../../../../tmp/owned",
    ])
    def test_a_crafted_identifier_stays_inside_the_archive(self, archive, crafted):
        link = media.store(archive, crafted, b"x", ext="png", when=MARCH)

        written = list(p for p in archive.rglob("*") if p.is_file())
        assert len(written) == 1
        assert archive.resolve() in written[0].resolve().parents
        assert ".." not in link

    def test_an_extension_cannot_carry_a_path_either(self, archive):
        media.store(archive, "$abc", b"x", ext="../../png", when=MARCH)

        written = [p for p in archive.rglob("*") if p.is_file()]
        assert written[0].name == "abc.png"

    def test_an_identifier_with_nothing_usable_still_files(self, archive):
        """Dropping the artifact would be the worse outcome. It lands
        under a generic name and the page still points at it."""
        link = media.store(archive, "///", b"x", ext="png", when=MARCH)

        assert link == "/media/2026/03/artifact.png"

    def test_two_different_identifiers_do_not_collide(self, archive):
        first = media.store(archive, "$aaa", b"1", ext="png", when=MARCH)
        second = media.store(archive, "$bbb", b"2", ext="png", when=MARCH)

        assert first != second


# ── Keeping the archive out of git ───────────────────────────────────────

class TestEnsureIgnored:
    """A committed blob survives every later deletion. The archive holds
    the only copy of bytes a deletion upstream has to be able to remove,
    so the repository that carries the tree must not track it."""

    def _repo(self, tmp_path: Path) -> Path:
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        return tmp_path

    def _ignored(self, repo: Path, rel: str) -> bool:
        done = subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "-q", rel], check=False,
        )
        return done.returncode == 0

    def test_git_ignores_the_archive_afterwards(self, tmp_path):
        repo = self._repo(tmp_path)
        media.store(media.archive_root(repo), "$a", b"x", ext="png", when=MARCH)

        media.ensure_ignored(repo)

        assert self._ignored(repo, "media/2026/03/a.png")

    def test_calling_it_again_does_not_repeat_the_rule(self, tmp_path):
        """It runs on every write, so a second call has to be a no-op.
        A .gitignore that grows a line per upload is a diff on every
        curator commit."""
        repo = self._repo(tmp_path)

        media.ensure_ignored(repo)
        once = (repo / ".gitignore").read_text()
        media.ensure_ignored(repo)

        assert (repo / ".gitignore").read_text() == once

    def test_rules_already_in_the_file_are_kept(self, tmp_path):
        """The seeded projection repo ships its own ignore rules. Adding
        one must not cost the others."""
        repo = self._repo(tmp_path)
        (repo / ".gitignore").write_text(".obsidian/\n.DS_Store\n")

        media.ensure_ignored(repo)

        assert self._ignored(repo, ".obsidian/workspace.json")
        assert self._ignored(repo, "media/2026/03/a.png")

    def test_a_file_without_a_trailing_newline_is_not_joined_onto(self, tmp_path):
        """Appending to `.DS_Store` would produce `.DS_Storemedia/` and
        silently ignore neither."""
        repo = self._repo(tmp_path)
        (repo / ".gitignore").write_text(".DS_Store")

        media.ensure_ignored(repo)

        assert self._ignored(repo, ".DS_Store")
        assert self._ignored(repo, "media/2026/03/a.png")


# ── Playable audio ───────────────────────────────────────────────────────

class TestTranscodeAudio:

    def test_a_missing_converter_is_reported_not_raised(self, tmp_path, monkeypatch):
        """ffmpeg is a container dependency and an instance can be
        mid-upgrade. The compile drops the second copy and keeps
        running; it never dies on the way to a page."""
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        src = tmp_path / "voice.ogg"
        src.write_bytes(b"not really audio")

        assert media.transcode_audio(src, tmp_path / "voice.m4a") is False
        assert not (tmp_path / "voice.m4a").exists()

    def test_unconvertible_input_leaves_nothing_behind(self, tmp_path):
        """A half-written file under the final name would be served as
        the recording, and would report itself as already converted on
        the next run."""
        src = tmp_path / "voice.ogg"
        src.write_bytes(b"not really audio")

        assert media.transcode_audio(src, tmp_path / "voice.m4a") is False
        assert list(tmp_path.glob("*.m4a")) == []
        assert list(tmp_path.glob(".tmp-*")) == []
