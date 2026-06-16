"""Unit tests for the backup orchestrator helpers.

Covers the pure-Python parts of cli/_orchestrator.py: source discovery,
target parsing, source serialization, engine command building, result
reading, and Matrix notification formatting. The actual engine
invocation (subprocess) and Matrix posting (live network) are skipped
— those are integration territory.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "stacklets" / "backup" / "cli"))

from _orchestrator import (
    SourceRecord,
    build_engine_command,
    discover_archive_sources,
    format_notification,
    get_targets,
    read_latest_run,
    serialize_sources_env,
)


# ── Fixture helpers ────────────────────────────────────────────────────────

def _make_fake_stacklet(
    root: Path,
    stacklet_id: str,
    archives: list,
    enabled: bool = True,
    name: str | None = None,
) -> None:
    """Create a stacklet manifest under root/stacklets/<id>/stacklet.toml
    with the given [[backup.archive]] entries. Optionally mark the
    stacklet as enabled by creating its setup-done marker."""
    stacklets_dir = root / "stacklets" / stacklet_id
    stacklets_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        f'id = "{stacklet_id}"',
        f'name = "{name or stacklet_id.title()}"',
        'category = "media"',
        'version = "0.1.0"',
    ]
    for archive in archives:
        lines.append("")
        lines.append("[[backup.archive]]")
        lines.append(f'name = "{archive["name"]}"')
        lines.append(f'path = "{archive["path"]}"')
        if "min_files" in archive:
            lines.append(f'min_files = {archive["min_files"]}')

    (stacklets_dir / "stacklet.toml").write_text("\n".join(lines) + "\n")

    if enabled:
        marker_dir = root / ".stack"
        marker_dir.mkdir(parents=True, exist_ok=True)
        (marker_dir / f"{stacklet_id}.setup-done").write_text("")


# ── Source discovery ───────────────────────────────────────────────────────

class TestDiscoverArchiveSources:
    def test_finds_archive_entries_from_enabled_stacklets(self, tmp_path):
        _make_fake_stacklet(
            tmp_path, "photos",
            [{"name": "library", "path": "{data_dir}/photos/library/library", "min_files": 10}],
            name="Photos",
        )
        sources = discover_archive_sources(
            tmp_path, tmp_path, Path("/var/famstack-data")
        )
        assert len(sources) == 1
        s = sources[0]
        assert s.id == "photos/library"
        assert s.display == "Photos"
        assert s.src_path == Path("/var/famstack-data/photos/library/library")
        assert s.vault_subdir == "data/photos-library"
        assert s.min_files == 10

    def test_skips_unenabled_stacklets(self, tmp_path):
        # Enabled photos contributes; disabled docs does not.
        _make_fake_stacklet(
            tmp_path, "photos",
            [{"name": "library", "path": "{data_dir}/photos", "min_files": 1}],
            enabled=True,
        )
        _make_fake_stacklet(
            tmp_path, "docs",
            [{"name": "media", "path": "{data_dir}/docs", "min_files": 1}],
            enabled=False,
        )
        sources = discover_archive_sources(tmp_path, tmp_path, Path("/d"))
        assert [s.id for s in sources] == ["photos/library"]

    def test_stacklets_without_backup_archive_skipped(self, tmp_path):
        # photos has no [[backup.archive]] declaration at all.
        stacklets_dir = tmp_path / "stacklets" / "photos"
        stacklets_dir.mkdir(parents=True)
        (stacklets_dir / "stacklet.toml").write_text(
            'id = "photos"\nname = "Photos"\n'
        )
        (tmp_path / ".stack").mkdir()
        (tmp_path / ".stack" / "photos.setup-done").write_text("")

        assert discover_archive_sources(tmp_path, tmp_path, Path("/d")) == []

    def test_multiple_archives_per_stacklet(self, tmp_path):
        _make_fake_stacklet(
            tmp_path, "photos",
            [
                {"name": "library", "path": "{data_dir}/a", "min_files": 1},
                {"name": "shared",  "path": "{data_dir}/b", "min_files": 1},
            ],
        )
        sources = discover_archive_sources(tmp_path, tmp_path, Path("/d"))
        assert [s.id for s in sources] == ["photos/library", "photos/shared"]
        assert [s.vault_subdir for s in sources] == ["data/photos-library", "data/photos-shared"]

    def test_template_variable_renders(self, tmp_path):
        # {data_dir} must expand to whatever the orchestrator was given.
        _make_fake_stacklet(
            tmp_path, "photos",
            [{"name": "library", "path": "{data_dir}/photos/library", "min_files": 1}],
        )
        sources = discover_archive_sources(
            tmp_path, tmp_path, Path("/totally/custom/data")
        )
        assert sources[0].src_path == Path("/totally/custom/data/photos/library")

    def test_unknown_template_variable_kept_literal(self, tmp_path):
        # A typo'd template var shouldn't crash discovery — the engine's
        # preflight will surface "directory not found" with a useful
        # error pointing at the broken path.
        _make_fake_stacklet(
            tmp_path, "photos",
            [{"name": "library", "path": "{nonexistent_var}/photos", "min_files": 1}],
        )
        sources = discover_archive_sources(tmp_path, tmp_path, Path("/d"))
        # The format() call raises KeyError, we fall back to the raw string.
        assert "{nonexistent_var}" in str(sources[0].src_path) or sources[0].src_path == Path("{nonexistent_var}/photos")

    def test_returns_empty_when_no_stacklets_dir(self, tmp_path):
        # tmp_path is empty — no stacklets/ subdirectory exists.
        assert discover_archive_sources(tmp_path, tmp_path, Path("/d")) == []

    def test_malformed_manifest_skipped_not_fatal(self, tmp_path):
        # A broken manifest in one stacklet shouldn't take down discovery
        # of all the others.
        _make_fake_stacklet(
            tmp_path, "photos",
            [{"name": "library", "path": "{data_dir}/p", "min_files": 1}],
        )
        broken = tmp_path / "stacklets" / "broken"
        broken.mkdir(parents=True)
        (broken / "stacklet.toml").write_text("this is not [valid] toml = ===")
        (tmp_path / ".stack" / "broken.setup-done").write_text("")

        sources = discover_archive_sources(tmp_path, tmp_path, Path("/d"))
        assert [s.id for s in sources] == ["photos/library"]


# ── Target discovery ───────────────────────────────────────────────────────

class TestGetTargets:
    def test_parses_a_target_block(self):
        cfg = {
            "backup": {
                "targets": {
                    "vault": {
                        "engine": "external-disk",
                        "disk": "backup-vault",
                        "schedule": "0 2 * * *",
                    }
                }
            }
        }
        targets = get_targets(cfg)
        assert len(targets) == 1
        t = targets[0]
        assert t.name == "vault"
        assert t.engine == "external-disk"
        assert t.disk == "backup-vault"
        assert t.schedule == "0 2 * * *"

    def test_returns_empty_when_no_backup_section(self):
        assert get_targets({}) == []

    def test_returns_empty_when_no_targets(self):
        assert get_targets({"backup": {}}) == []

    def test_skips_targets_missing_engine(self):
        # A target without an engine is malformed — we'd rather skip
        # than silently pick a default.
        cfg = {
            "backup": {
                "targets": {
                    "ok": {"engine": "external-disk", "disk": "a"},
                    "broken": {"disk": "b"},  # no engine
                }
            }
        }
        targets = get_targets(cfg)
        assert [t.name for t in targets] == ["ok"]

    def test_multiple_targets(self):
        cfg = {
            "backup": {
                "targets": {
                    "vault": {"engine": "external-disk", "disk": "vault"},
                    "offsite": {"engine": "restic", "disk": ""},
                }
            }
        }
        names = sorted(t.name for t in get_targets(cfg))
        assert names == ["offsite", "vault"]


# ── Source serialization ───────────────────────────────────────────────────

class TestSerializeSourcesEnv:
    def test_single_record_formatted(self):
        sources = [SourceRecord(
            id="photos/library", display="Photos",
            src_path=Path("/var/famstack-data/photos/library/library"),
            vault_subdir="data/photos-library", min_files=10,
        )]
        env = serialize_sources_env(sources)
        assert env == (
            "photos/library|Photos|/var/famstack-data/photos/library/library|"
            "data/photos-library|10"
        )

    def test_multiple_records_newline_joined(self):
        sources = [
            SourceRecord("photos/library", "Photos", Path("/a"), "data/p", 10),
            SourceRecord("docs/media", "Documents", Path("/b"), "data/d", 5),
        ]
        env = serialize_sources_env(sources)
        lines = env.split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("photos/library|")
        assert lines[1].startswith("docs/media|")

    def test_empty_input_yields_empty_string(self):
        assert serialize_sources_env([]) == ""


# ── Engine command ─────────────────────────────────────────────────────────

class TestBuildEngineCommand:
    def _args(self, **kw) -> argparse.Namespace:
        defaults = dict(dry_run=False, no_eject=False, verbose=False, verify=False)
        defaults.update(kw)
        return argparse.Namespace(**defaults)

    def test_no_flags(self):
        cmd = build_engine_command(Path("/tmp/sync.py"), self._args())
        # Last element is the script path; flags follow only if set.
        assert cmd[-1] == "/tmp/sync.py"
        assert "--dry-run" not in cmd

    def test_dry_run_added(self):
        cmd = build_engine_command(Path("/tmp/sync.py"), self._args(dry_run=True))
        assert "--dry-run" in cmd

    def test_all_flags_added(self):
        cmd = build_engine_command(
            Path("/tmp/sync.py"),
            self._args(dry_run=True, no_eject=True, verbose=True, verify=True),
        )
        assert "--dry-run" in cmd
        assert "--no-eject" in cmd
        assert "--verbose" in cmd
        assert "--verify" in cmd


# ── Result reading ─────────────────────────────────────────────────────────

class TestReadLatestRun:
    def test_returns_none_when_history_missing(self, tmp_path):
        # Engine crashed before it could append — distinct from "engine
        # reported a failure" which would have written a line.
        assert read_latest_run(tmp_path) is None

    def test_reads_only_line_when_one_run(self, tmp_path):
        (tmp_path / "logs").mkdir()
        (tmp_path / "logs" / "history.jsonl").write_text(
            '{"success": true, "sources": []}\n'
        )
        assert read_latest_run(tmp_path) == {"success": True, "sources": []}

    def test_reads_last_line_when_many_runs(self, tmp_path):
        # "Latest" = most recently appended.
        (tmp_path / "logs").mkdir()
        (tmp_path / "logs" / "history.jsonl").write_text(
            '{"success": true, "vault_disk": "first"}\n'
            '{"success": true, "vault_disk": "middle"}\n'
            '{"success": false, "vault_disk": "latest"}\n'
        )
        loaded = read_latest_run(tmp_path)
        assert loaded["vault_disk"] == "latest"
        assert loaded["success"] is False

    def test_returns_none_when_history_empty(self, tmp_path):
        (tmp_path / "logs").mkdir()
        (tmp_path / "logs" / "history.jsonl").write_text("")
        assert read_latest_run(tmp_path) is None

    def test_skips_corrupted_trailing_line(self, tmp_path):
        # A partial-write corruption (shouldn't happen with our atomic
        # appends, but defense in depth) must not lose the last good
        # run from the report.
        (tmp_path / "logs").mkdir()
        (tmp_path / "logs" / "history.jsonl").write_text(
            '{"success": true, "vault_disk": "good"}\n'
            '{"corrupted, no closing\n'
        )
        loaded = read_latest_run(tmp_path)
        assert loaded["vault_disk"] == "good"


# ── Notification formatting ────────────────────────────────────────────────

def _success_result(**overrides):
    """Build a baseline successful result dict; tests override fields."""
    base = {
        "success": True,
        "dry_run": False,
        "failure_reason": None,
        "duration_seconds": 125,
        "started_at": "2026-05-14T02:00:00Z",
        "ended_at": "2026-05-14T02:02:05Z",
        "run_context": "cron",
        "run_user": "arthur",
        "vault_disk": "backup-vault",
        "vault_state": "mounted",
        "vault_size": "8.2G",
        "sources": [
            {"id": "photos/library", "display": "Photos",
             "status": "ok", "total_files": 48293, "new_files": 12},
            {"id": "docs/media", "display": "Documents",
             "status": "ok", "total_files": 4421, "new_files": 3},
        ],
    }
    base.update(overrides)
    return base


class TestFormatNotification:
    def test_success_headline_in_both_bodies(self):
        plain, html = format_notification("vault", _success_result())
        assert plain.startswith("✅ Backup Sync Completed")
        assert "<b>✅ Backup Sync Completed</b>" in html

    def test_failure_headline_includes_reason(self):
        plain, html = format_notification("vault", _success_result(
            success=False, failure_reason="Canary check failed",
        ))
        assert plain.startswith("❌ Backup Sync FAILED")
        assert "Reason: Canary check failed" in plain
        assert "<b>❌ Backup Sync FAILED</b>" in html
        assert "Reason: Canary check failed" in html

    def test_dry_run_headline(self):
        plain, html = format_notification("vault", _success_result(dry_run=True))
        assert "dry run" in plain.lower()
        assert "dry run" in html.lower()

    def test_target_name_appears(self):
        plain, html = format_notification("offsite", _success_result())
        assert "Target: offsite" in plain
        assert "offsite" in html

    def test_duration_formatted_mm_ss(self):
        plain, _ = format_notification("vault", _success_result(duration_seconds=125))
        assert "2m 5s" in plain

    def test_source_counts_use_dot_separator(self):
        plain, html = format_notification("vault", _success_result())
        # Phone-number linkification in Element is the reason — dot,
        # not comma.
        assert "48.293" in plain
        assert "<b>48.293</b>" in html

    def test_mounted_state_notes_context(self):
        # The mounted-after-cron state isn't a failure; it's the
        # documented operational truth.
        plain, _ = format_notification("vault", _success_result(
            vault_state="mounted", run_context="cron"
        ))
        assert "mounted" in plain
        assert "cron" in plain

    def test_ejected_state(self):
        plain, _ = format_notification("vault", _success_result(vault_state="ejected"))
        assert "ejected" in plain.lower()

    def test_not_connected_state_warns(self):
        plain, _ = format_notification("vault", _success_result(
            success=False, vault_state="not_connected",
            failure_reason="Backup disk not connected",
        ))
        assert "not connected" in plain.lower()

    def test_failed_source_marked_in_output(self):
        result = _success_result(
            success=False,
            failure_reason="rsync failed for one source",
            sources=[
                {"id": "photos/library", "display": "Photos",
                 "status": "ok", "total_files": 100, "new_files": 5},
                {"id": "docs/media", "display": "Documents",
                 "status": "FAILED", "total_files": 0, "new_files": 0},
            ],
        )
        plain, html = format_notification("vault", result)
        assert "Documents: FAILED" in plain
        assert "<b>FAILED</b>" in html

    def test_no_sources_renders_cleanly(self):
        # Edge case: result with empty sources list (engine aborted
        # before sync_data ran). Should still produce a coherent
        # message rather than blowing up.
        plain, html = format_notification("vault", _success_result(sources=[]))
        assert "Target: vault" in plain
        assert "Target:" in html
