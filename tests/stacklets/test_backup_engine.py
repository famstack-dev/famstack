"""Unit tests for the external-disk backup engine.

Covers the pure-Python parts of the pipeline: source parsing, canary
behavior, preflight, filesystem capability probe, result-file shape.
Skips the rsync/diskutil/eject flows — those need a real disk and live
in integration tests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(
    REPO_ROOT / "stacklets" / "backup" / "engines" / "external-disk"
))

import sync as engine
from sync import (
    CANARY_STRING,
    Source,
    SourceResult,
    SyncAborted,
    SyncResult,
    append_to_history,
    format_number,
    parse_sources,
    preflight_check_sources,
    probe_filesystem,
    read_latest_run,
    verify_canary,
)


# ── Source parsing ─────────────────────────────────────────────────────────

class TestParseSources:
    def test_single_record(self):
        sources = parse_sources(
            "photos/library|Photos|/data/photos/library|data/photos-library|10"
        )
        assert len(sources) == 1
        s = sources[0]
        assert s.id == "photos/library"
        assert s.display == "Photos"
        assert s.src_path == Path("/data/photos/library")
        assert s.vault_subdir == "data/photos-library"
        assert s.min_files == 10

    def test_multiple_records_separated_by_newlines(self):
        sources = parse_sources(
            "photos/library|Photos|/a|data/p|10\n"
            "docs/media|Documents|/b|data/d|5"
        )
        assert [s.id for s in sources] == ["photos/library", "docs/media"]
        assert sources[1].min_files == 5

    def test_blank_lines_ignored(self):
        sources = parse_sources(
            "\n"
            "photos/library|Photos|/a|data/p|10\n"
            "   \n"
            "docs/media|Documents|/b|data/d|5\n"
        )
        assert len(sources) == 2

    def test_empty_input_aborts(self):
        # Empty $SOURCES is a misconfiguration, not a valid "no work to do."
        # We refuse loudly rather than silently doing nothing.
        with pytest.raises(SyncAborted, match="No sources provided"):
            parse_sources("")

    def test_whitespace_only_aborts(self):
        with pytest.raises(SyncAborted, match="No sources provided"):
            parse_sources("   \n\n   ")

    def test_too_few_fields_aborts(self):
        with pytest.raises(SyncAborted, match="Malformed source record"):
            parse_sources("photos/library|Photos|/a|data/p")  # only 4 fields

    def test_too_many_fields_aborts(self):
        with pytest.raises(SyncAborted, match="Malformed source record"):
            parse_sources("a|b|c|d|10|extra")

    def test_non_integer_min_files_aborts(self):
        with pytest.raises(SyncAborted, match="min_files must be an integer"):
            parse_sources("a|b|c|d|many")


# ── Canary ─────────────────────────────────────────────────────────────────

class TestVerifyCanary:
    def test_matching_content_passes(self, tmp_path, capsys):
        canary = tmp_path / "canary"
        canary.write_text(CANARY_STRING + "\n")
        verify_canary(canary)  # should not raise

    def test_missing_canary_aborts(self, tmp_path, capsys):
        canary = tmp_path / "canary"
        with pytest.raises(SyncAborted, match="missing"):
            verify_canary(canary)

    def test_content_mismatch_aborts(self, tmp_path, capsys):
        canary = tmp_path / "canary"
        canary.write_text("not the expected string\n")
        with pytest.raises(SyncAborted, match="Canary check failed"):
            verify_canary(canary)

    def test_trailing_whitespace_in_canary_is_tolerated(self, tmp_path, capsys):
        # The verifier strips before comparing, so a planter that
        # included an extra newline or trailing space doesn't trip
        # the corruption check.
        canary = tmp_path / "canary"
        canary.write_text(CANARY_STRING + "\n\n  ")
        verify_canary(canary)  # should not raise


# ── Preflight ──────────────────────────────────────────────────────────────

class TestPreflightCheckSources:
    def _make_source(self, tmp_path: Path, name: str, file_count: int, min_files: int) -> Source:
        src_dir = tmp_path / name
        src_dir.mkdir()
        for i in range(file_count):
            (src_dir / f"file-{i}.txt").write_text("x")
        return Source(
            id=f"test/{name}",
            display=name.title(),
            src_path=src_dir,
            vault_subdir=f"data/test-{name}",
            min_files=min_files,
        )

    def test_passes_when_each_source_meets_min(self, tmp_path, capsys):
        sources = [
            self._make_source(tmp_path, "a", file_count=20, min_files=10),
            self._make_source(tmp_path, "b", file_count=15, min_files=10),
        ]
        preflight_check_sources(sources)  # should not raise

    def test_aborts_when_any_source_under_min(self, tmp_path, capsys):
        sources = [
            self._make_source(tmp_path, "ok", file_count=20, min_files=10),
            self._make_source(tmp_path, "low", file_count=2, min_files=10),
        ]
        with pytest.raises(SyncAborted, match="Preflight failed"):
            preflight_check_sources(sources)

    def test_aborts_when_source_dir_missing(self, tmp_path, capsys):
        source = Source(
            id="test/missing",
            display="Missing",
            src_path=tmp_path / "does-not-exist",
            vault_subdir="data/test-missing",
            min_files=1,
        )
        with pytest.raises(SyncAborted, match="Preflight failed"):
            preflight_check_sources([source])

    def test_exact_min_count_passes(self, tmp_path, capsys):
        # Edge: file_count == min_files should pass (not "strictly greater than").
        sources = [self._make_source(tmp_path, "exact", file_count=10, min_files=10)]
        preflight_check_sources(sources)  # should not raise


# ── _stat_fs_type (mocked mount output) ────────────────────────────────────

class TestStatFsType:
    """Direct tests for the engine's mount-parsing. Lives alongside
    TestProbeFilesystem (which mocks the return value) so a future
    regression in the parser fails here loudly, not via opaque probe
    behavior."""

    def _mount(self, *lines):
        return "\n".join(lines) + "\n"

    def test_apfs_extracted(self):
        from unittest.mock import patch
        out = self._mount(
            "/dev/disk3s1 on / (apfs, sealed)",
            "/dev/disk5s1 on /Volumes/foo (apfs, local, nodev)",
        )
        with patch.object(engine.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = out
            assert engine._stat_fs_type(Path("/Volumes/foo")) == "apfs"

    def test_smbfs_extracted(self):
        from unittest.mock import patch
        out = self._mount(
            "//u@host/share on /Volumes/share (smbfs, nodev)"
        )
        with patch.object(engine.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = out
            assert engine._stat_fs_type(Path("/Volumes/share")) == "smbfs"

    def test_empty_when_not_in_mount_output(self):
        from unittest.mock import patch
        out = self._mount("/dev/disk3s1 on / (apfs, sealed)")
        with patch.object(engine.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = out
            assert engine._stat_fs_type(Path("/Volumes/absent")) == ""

    def test_partial_path_prefix_does_not_match(self):
        from unittest.mock import patch
        out = self._mount("/dev/disk5s1 on /Volumes/foobar (apfs, local)")
        with patch.object(engine.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = out
            assert engine._stat_fs_type(Path("/Volumes/foo")) == ""


# ── Filesystem capability ──────────────────────────────────────────────────

class TestProbeFilesystem:
    @pytest.fixture
    def mock_fs_type(self, monkeypatch):
        """Patch _stat_fs_type to return whatever string the test wants.

        Probing real filesystems isn't reproducible in CI, and stat -f
        output is the only seam that matters here — what probe_filesystem
        DOES with the type string is the testable behavior.
        """
        def factory(fs_type: str):
            monkeypatch.setattr(engine, "_stat_fs_type", lambda _path: fs_type)
        return factory

    def test_apfs_passes(self, mock_fs_type, tmp_path, capsys):
        mock_fs_type("apfs")
        probe_filesystem(tmp_path)  # should not raise

    def test_hfs_passes(self, mock_fs_type, tmp_path, capsys):
        mock_fs_type("hfs")
        probe_filesystem(tmp_path)

    def test_smbfs_aborts_with_network_message(self, mock_fs_type, tmp_path, capsys):
        mock_fs_type("smbfs")
        with pytest.raises(SyncAborted, match="does not support BSD immutability"):
            probe_filesystem(tmp_path)
        # Make sure the user-facing message points at the future restic engine.
        err = capsys.readouterr().err
        assert "restic" in err

    def test_nfs_aborts(self, mock_fs_type, tmp_path, capsys):
        mock_fs_type("nfs")
        with pytest.raises(SyncAborted, match="does not support BSD immutability"):
            probe_filesystem(tmp_path)

    def test_exfat_aborts_with_reformat_message(self, mock_fs_type, tmp_path, capsys):
        mock_fs_type("exfat")
        with pytest.raises(SyncAborted, match="does not support BSD immutability"):
            probe_filesystem(tmp_path)
        err = capsys.readouterr().err
        assert "Reformat" in err

    def test_msdos_aborts(self, mock_fs_type, tmp_path, capsys):
        mock_fs_type("msdos")
        with pytest.raises(SyncAborted):
            probe_filesystem(tmp_path)

    def test_unknown_fs_aborts(self, mock_fs_type, tmp_path, capsys):
        # Future filesystems we haven't classified should be refused too —
        # the "supported list is the only safe list" principle.
        mock_fs_type("zfs")
        with pytest.raises(SyncAborted, match="not supported"):
            probe_filesystem(tmp_path)

    def test_empty_fs_type_aborts(self, mock_fs_type, tmp_path, capsys):
        # stat -f failed (returned ""). Treat like unknown — refuse.
        mock_fs_type("")
        with pytest.raises(SyncAborted):
            probe_filesystem(tmp_path)


# ── Number formatting ──────────────────────────────────────────────────────

class TestFormatNumber:
    def test_under_thousand_unchanged(self):
        assert format_number(0) == "0"
        assert format_number(42) == "42"
        assert format_number(999) == "999"

    def test_thousands_separated_with_dot(self):
        # Dot, not comma — comma triggers phone-number linkification in
        # some chat clients (Element among them).
        assert format_number(1000) == "1.000"
        assert format_number(48293) == "48.293"
        assert format_number(1_234_567) == "1.234.567"


# ── Result writing ─────────────────────────────────────────────────────────

class TestAppendToHistory:
    def _result(self, **overrides) -> SyncResult:
        defaults = dict(
            success=True,
            dry_run=False,
            duration_seconds=125,
            started_at="2026-05-14T02:00:00Z",
            ended_at="2026-05-14T02:02:05Z",
            run_context="cron",
            run_user="arthur",
            vault_disk="backup-vault",
            vault_state="mounted",
            vault_size="8.2G",
            sources=[
                SourceResult(
                    id="photos/library", display="Photos",
                    status="ok", total_files=48293, new_files=12,
                ),
            ],
        )
        defaults.update(overrides)
        return SyncResult(**defaults)

    def test_writes_one_jsonl_line_per_run(self, tmp_path):
        history = tmp_path / "logs" / "history.jsonl"
        append_to_history(self._result(), history)

        lines = history.read_text().splitlines()
        assert len(lines) == 1
        loaded = json.loads(lines[0])
        assert loaded["success"] is True
        assert loaded["sources"][0]["id"] == "photos/library"
        assert loaded["sources"][0]["total_files"] == 48293

    def test_append_does_not_overwrite_prior_lines(self, tmp_path):
        # Two successive appends must produce two distinct lines —
        # never a JSON array rewrite, never an overwrite. The append-
        # only contract is the whole reason we picked JSONL.
        history = tmp_path / "logs" / "history.jsonl"
        append_to_history(self._result(success=True), history)
        append_to_history(self._result(success=False, failure_reason="Disk full"), history)

        lines = history.read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["success"] is True
        assert json.loads(lines[1])["success"] is False
        assert json.loads(lines[1])["failure_reason"] == "Disk full"

    def test_empty_failure_reason_serializes_as_null(self, tmp_path):
        # Callers shouldn't have to distinguish "" from "no failure
        # recorded" — coerce empty/missing to JSON null on write.
        history = tmp_path / "history.jsonl"
        append_to_history(SyncResult(success=True, failure_reason=None), history)
        loaded = json.loads(history.read_text())
        assert loaded["failure_reason"] is None

    def test_creates_parent_directory(self, tmp_path):
        # The history file usually lives under BACKUP_DATA_DIR/logs/ —
        # the parent directory may not exist on the very first run.
        history = tmp_path / "deeply" / "nested" / "history.jsonl"
        append_to_history(self._result(), history)
        assert history.exists()

    def test_each_line_terminates_with_newline(self, tmp_path):
        # Newline-per-record is what makes JSONL JSONL. Without it, a
        # second append would land on the same line as the first.
        history = tmp_path / "history.jsonl"
        append_to_history(self._result(), history)
        content = history.read_text()
        assert content.endswith("\n")


class TestReadLatestRun:
    def _result(self, **overrides) -> SyncResult:
        defaults = dict(success=True, started_at="2026-05-14T02:00:00Z")
        defaults.update(overrides)
        return SyncResult(**defaults)

    def test_returns_none_when_file_missing(self, tmp_path):
        assert read_latest_run(tmp_path / "history.jsonl") is None

    def test_returns_none_when_file_empty(self, tmp_path):
        history = tmp_path / "history.jsonl"
        history.write_text("")
        assert read_latest_run(history) is None

    def test_returns_single_run_from_one_line(self, tmp_path):
        history = tmp_path / "history.jsonl"
        append_to_history(self._result(vault_disk="backup-vault"), history)
        loaded = read_latest_run(history)
        assert loaded is not None
        assert loaded["vault_disk"] == "backup-vault"

    def test_returns_the_last_line_when_multiple_runs(self, tmp_path):
        # "Latest" means most recently appended, which in append-only
        # JSONL is the last line.
        history = tmp_path / "history.jsonl"
        append_to_history(self._result(vault_disk="run-1"), history)
        append_to_history(self._result(vault_disk="run-2"), history)
        append_to_history(self._result(vault_disk="run-3"), history)
        loaded = read_latest_run(history)
        assert loaded["vault_disk"] == "run-3"

    def test_skips_corrupted_trailing_line(self, tmp_path):
        # A crashed engine COULD in theory leave a partial line. Our
        # writes are sub-PIPE_BUF so this shouldn't happen, but the
        # reader must be tolerant — return the last GOOD line, not
        # None, when the trailing line is unparseable.
        history = tmp_path / "history.jsonl"
        append_to_history(self._result(vault_disk="good-run"), history)
        # Manually corrupt the trailing record (without going through append)
        with history.open("a") as f:
            f.write('{"bad json, no closing\n')

        loaded = read_latest_run(history)
        assert loaded is not None
        assert loaded["vault_disk"] == "good-run"

    def test_skips_blank_lines(self, tmp_path):
        # Defensive: editor-introduced blank lines or stray newlines
        # shouldn't break the scan.
        history = tmp_path / "history.jsonl"
        append_to_history(self._result(vault_disk="r1"), history)
        with history.open("a") as f:
            f.write("\n\n")
        append_to_history(self._result(vault_disk="r2"), history)
        with history.open("a") as f:
            f.write("\n")

        loaded = read_latest_run(history)
        assert loaded["vault_disk"] == "r2"


# ── Vault state ────────────────────────────────────────────────────────────

class TestDetectVaultState:
    def test_drive_not_connected_takes_priority(self, tmp_path):
        # Even if the mount point happens to exist for some reason, the
        # "drive isn't there" signal wins.
        assert engine.detect_vault_state(tmp_path, drive_not_connected=True) == "not_connected"

    def test_mounted_when_mount_point_exists(self, tmp_path):
        assert engine.detect_vault_state(tmp_path, drive_not_connected=False) == "mounted"

    def test_ejected_when_mount_point_missing(self, tmp_path):
        assert engine.detect_vault_state(tmp_path / "missing", drive_not_connected=False) == "ejected"


# ── Sync data: lock enforcement ────────────────────────────────────────────

class TestSyncDataLock:
    """The chflags lock IS the append-only guarantee. sync_data must
    report FAILED — not a silent ok — when the lock can't be applied.

    rsync and chflags are mocked: rsync's mock writes a real file into
    the dest so count_files sees new files (driving the lock branch);
    chflags's mock chooses success or failure per test.
    """

    def _source(self, tmp_path: Path) -> Source:
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.jpg").write_text("x")
        return Source(id="photos/library", display="Photos",
                      src_path=src, vault_subdir="data/photos-library",
                      min_files=1)

    def _fake_run(self, chflags_returncode: int):
        from types import SimpleNamespace

        def run(cmd, *a, **kw):
            prog = cmd[0]
            if prog == "/usr/bin/rsync":
                dest = Path(cmd[-1])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "a.jpg").write_text("x")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if prog == "find":
                return SimpleNamespace(
                    returncode=chflags_returncode,
                    stdout="",
                    stderr="chflags: Operation not permitted" if chflags_returncode else "",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        return run

    def test_lock_failure_marks_source_failed(self, tmp_path, capsys):
        from unittest.mock import patch

        mount = tmp_path / "vault"
        mount.mkdir()
        log = tmp_path / "logs" / "sync.log"
        sources = [self._source(tmp_path)]

        with patch.object(engine.subprocess, "run",
                          side_effect=self._fake_run(chflags_returncode=1)):
            results = engine.sync_data(sources, mount, log, dry_run=False, verbose=False)

        assert results[0].status == "FAILED"
        # error() writes to stderr.
        assert "append-only protection not applied" in capsys.readouterr().err

    def test_lock_success_marks_source_ok(self, tmp_path):
        from unittest.mock import patch

        mount = tmp_path / "vault"
        mount.mkdir()
        log = tmp_path / "logs" / "sync.log"
        sources = [self._source(tmp_path)]

        with patch.object(engine.subprocess, "run",
                          side_effect=self._fake_run(chflags_returncode=0)):
            results = engine.sync_data(sources, mount, log, dry_run=False, verbose=False)

        assert results[0].status == "ok"
        assert results[0].new_files == 1
