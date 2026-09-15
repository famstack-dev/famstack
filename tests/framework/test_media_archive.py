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

import hashlib
import json
import shutil
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


# ── The record beside the file ───────────────────────────────────────────

class TestKeep:
    """An archive nobody can read back is a pile of anonymous files.
    `keep` writes the file and the record of where it came from, and
    that record is the only thing in the tree that still knows."""

    ORIGIN = {
        "kind": "image", "source": "capture", "mime": "image/png",
        "filename": "Bildschirmfoto 2026-03-14.png",
        "room_id": "!kitchen:example.org", "sender": "@marge:example.org",
    }

    def _record(self, archive: Path) -> dict:
        return json.loads((archive / "2026" / "03" / "abc.json")
                          .read_text(encoding="utf-8"))

    def test_the_record_says_who_posted_it_where_and_under_what_name(self, archive):
        """Everything needed to put the file back in its context, next
        to the file. The name in the tree is a scrubbed identifier and
        the folders are a date, so nothing else in the archive carries
        any of this."""
        media.keep(archive, "$abc", b"bytes", ext="png",
                   when=datetime(2026, 3, 14, 9, 30, tzinfo=timezone.utc),
                   **self.ORIGIN)

        record = self._record(archive)
        assert record["event_id"] == "$abc", "unscrubbed, or the timeline is lost"
        assert record["room_id"] == "!kitchen:example.org"
        assert record["sender"] == "@marge:example.org"
        assert record["filename"] == "Bildschirmfoto 2026-03-14.png"
        assert record["captured_at"].startswith("2026-03-14T09:30")
        assert record["source"] == "capture"

    def test_the_record_can_prove_the_bytes_are_the_bytes(self, archive):
        media.keep(archive, "$abc", b"bytes", ext="png", when=MARCH, **self.ORIGIN)

        record = self._record(archive)
        assert record["size"] == 5
        assert record["sha256"] == hashlib.sha256(b"bytes").hexdigest()

    def test_a_second_compile_over_the_same_room_changes_nothing(self, archive):
        """The diary recompiles the whole room every night. A run that
        rewrote every file it had already kept would be a diff of the
        entire archive, every night, forever."""
        media.keep(archive, "$abc", b"bytes", ext="png", when=MARCH, **self.ORIGIN)
        month = archive / "2026" / "03"
        before = {p.name: p.stat().st_mtime_ns for p in month.iterdir()}

        media.keep(archive, "$abc", b"bytes", ext="png", when=MARCH, **self.ORIGIN)

        assert {p.name: p.stat().st_mtime_ns for p in month.iterdir()} == before

    def test_bytes_that_cannot_be_written_leave_no_record_saying_they_were(
            self, tmp_path):
        blocked = tmp_path / "file-where-a-directory-should-be"
        blocked.write_text("not a directory")

        assert media.keep(blocked, "$abc", b"x", ext="png", when=MARCH,
                          **self.ORIGIN) == ""


class TestKnowingWhatIsAlreadyThere:

    def test_an_artifact_in_the_archive_is_reported_without_its_bytes(self, archive):
        """The compiler asks before it downloads. A room with years of
        photographs in it would otherwise be re-fetched in full on every
        run to write files that are already on disk."""
        assert media.kept(archive, "$abc", ext="png", when=MARCH) == ""

        media.store(archive, "$abc", b"bytes", ext="png", when=MARCH)

        assert media.kept(archive, "$abc", ext="png", when=MARCH) == \
            "/media/2026/03/abc.png"


class TestNamingAnArtifact:
    """The extension is not cosmetic: the wiki turns an embed into an
    audio player, a video player or an image by reading it off the
    path, so getting it wrong loses the player."""

    @pytest.mark.parametrize("filename, mime, expected", [
        ("voice-message.ogg", "audio/ogg", "ogg"),
        ("IMG_4021.JPEG", "image/jpeg", "jpeg"),
        ("Zeugnis Bart.pdf", "application/pdf", "pdf"),
        ("", "image/png", "png"),
        ("", "video/mp4", "mp4"),
        ("", "", "bin"),
        ("noextension", "", "bin"),
    ])
    def test_the_name_it_was_posted_as_decides(self, filename, mime, expected):
        assert media.extension_for(filename, mime) == expected

    @pytest.mark.parametrize("mime, expected", [
        ("image/png", "image"), ("video/quicktime", "video"),
        ("audio/ogg; codecs=opus", "audio"), ("application/pdf", "file"),
        ("", "file"),
    ])
    def test_the_kind_follows_the_media_type(self, mime, expected):
        assert media.kind_for(mime) == expected


# ── Second copies ────────────────────────────────────────────────────────

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg is a container dependency; absent here")

def _width(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width", "-of", "csv=p=0", str(path)],
        capture_output=True, check=True,
    )
    return int(out.stdout.decode().strip())


def _an_image(path: Path, width: int) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc=size={width}x{width // 2}:duration=1",
         "-frames:v", "1", str(path)], check=True)
    return path.read_bytes()


@needs_ffmpeg
class TestBoundingAnImage:
    """Twenty originals off a phone are a hundred megabytes on one month
    page. The width alias a wikilink carries is a display attribute and
    changes nothing about what crosses the wire, so the file itself has
    to be smaller."""

    def test_a_large_photograph_comes_out_at_the_bound(self, tmp_path):
        _an_image(tmp_path / "big.png", 3000)

        assert media.transcode_image(
            tmp_path / "big.png", tmp_path / "out.png") is True
        assert _width(tmp_path / "out.png") == media.MAX_IMAGE_WIDTH

    def test_a_small_photograph_is_not_blown_up_to_fill_it(self, tmp_path):
        """An old 400px photo enlarged to 1600 is a bigger file showing
        less. The bound is a ceiling, not a target."""
        _an_image(tmp_path / "small.png", 400)

        media.transcode_image(tmp_path / "small.png", tmp_path / "out.png")

        assert _width(tmp_path / "out.png") == 400

    def test_the_original_survives_being_derived_from(self, tmp_path):
        before = _an_image(tmp_path / "photo.png", 800)

        media.transcode_image(tmp_path / "photo.png", tmp_path / "out.png")

        assert (tmp_path / "photo.png").read_bytes() == before


class TestDerivatives:
    """A page embeds the derivative, so the derivative is what decides
    whether a reader hears a recording, and how much of a photograph
    they have to download to see it."""

    ORIGIN = {"source": "diary", "room_id": "!memories:example.org",
              "sender": "@homer:example.org"}

    @needs_ffmpeg
    def test_a_photograph_gets_a_bounded_copy_beside_it(self, archive, tmp_path):
        data = _an_image(tmp_path / "photo.png", 3000)
        media.keep(archive, "$photo", data, ext="png", when=MARCH,
                   kind="image", mime="image/png", filename="photo.png",
                   **self.ORIGIN)

        link = media.derive(archive, "$photo", ext="png", when=MARCH, kind="image")

        assert link == "/media/2026/03/photo.jpg"
        assert _width(archive / "2026" / "03" / "photo.jpg") == media.MAX_IMAGE_WIDTH
        assert (archive / "2026" / "03" / "photo.png").read_bytes() == data

    def test_a_derivative_is_written_into_the_record_that_owns_it(self, archive):
        """A re-encode has to be able to replace its own output. Without
        the list the previous file stays in the tree with nothing
        pointing at it and nothing saying where it came from."""
        media.keep(archive, "$voice", b"ogg bytes", ext="ogg", when=MARCH,
                   kind="audio", mime="audio/ogg", filename="voice.ogg",
                   **self.ORIGIN)
        # As an earlier compile left it: the conversion is ffmpeg's, the
        # bookkeeping is ours, and only the bookkeeping is under test.
        (archive / "2026" / "03" / "voice.m4a").write_bytes(b"aac bytes")
        payload = archive / "2026" / "03" / "voice.ogg"
        before = payload.stat().st_mtime_ns

        link = media.derive(archive, "$voice", ext="ogg", when=MARCH, kind="audio")
        media.derive(archive, "$voice", ext="ogg", when=MARCH, kind="audio")

        record = json.loads((archive / "2026" / "03" / "voice.json")
                            .read_text(encoding="utf-8"))
        assert record["derived"] == [{"path": link, "kind": "audio"}], \
            "listed once, however often the compile runs"
        assert payload.stat().st_mtime_ns == before, "the original is not rewritten"

    def test_an_artifact_already_in_the_derived_form_gets_no_second_copy(self, archive):
        """Deriving an .m4a to an .m4a would name the conversion's
        output as its own input."""
        media.keep(archive, "$voice", b"aac bytes", ext="m4a", when=MARCH,
                   kind="audio", mime="audio/mp4", filename="voice.m4a",
                   **self.ORIGIN)

        assert media.derive(archive, "$voice", ext="m4a", when=MARCH,
                            kind="audio") == ""
        assert (archive / "2026" / "03" / "voice.m4a").read_bytes() == b"aac bytes"

    def test_an_artifact_that_has_no_second_form_asks_for_none(self, archive):
        media.keep(archive, "$doc", b"%PDF-", ext="pdf", when=MARCH,
                   kind="file", mime="application/pdf", filename="form.pdf",
                   **self.ORIGIN)

        assert media.derive(archive, "$doc", ext="pdf", when=MARCH,
                            kind="file") == ""

    def test_without_a_converter_the_page_loses_the_player_not_the_entry(
            self, archive, monkeypatch, tmp_path):
        """ffmpeg is a container dependency and an instance can be
        mid-upgrade. The compile keeps the original, skips the second
        copy and publishes; it never dies on the way to a page."""
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        media.keep(archive, "$voice", b"ogg bytes", ext="ogg", when=MARCH,
                   kind="audio", mime="audio/ogg", filename="voice.ogg",
                   **self.ORIGIN)

        assert media.derive(archive, "$voice", ext="ogg", when=MARCH,
                            kind="audio") == ""

        month = archive / "2026" / "03"
        assert sorted(p.name for p in month.iterdir()) == ["voice.json", "voice.ogg"]
        record = json.loads((month / "voice.json").read_text(encoding="utf-8"))
        assert record["derived"] == []
