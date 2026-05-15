"""Unit tests for on_install's pure helpers: canary planting and the
cron command builder.

The interactive FDA walkthrough is integration-only (requires a TTY +
System Settings) and isn't covered here. Crontab plumbing has its own
tests in test_backup_cron.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "stacklets" / "backup" / "hooks"))
sys.path.insert(0, str(REPO_ROOT / "lib"))

import on_install  # noqa: E402


class TestPlantCanary:
    def test_writes_canary_with_expected_content(self, tmp_path):
        on_install.plant_canary(tmp_path)
        canary = tmp_path / "canary"
        assert canary.is_file()
        assert canary.read_text().strip() == on_install.CANARY_STRING

    def test_idempotent_does_not_clobber_existing(self, tmp_path):
        # An existing canary that's already been verified across syncs
        # must survive a re-run of install. Clobbering it would make a
        # tampered state indistinguishable from a fresh install.
        canary = tmp_path / "canary"
        canary.write_text("user-edited content (or already-verified canary)\n")
        on_install.plant_canary(tmp_path)
        assert canary.read_text() == "user-edited content (or already-verified canary)\n"

    def test_canary_string_matches_engine(self):
        # The planter writes what the verifier expects — they share the
        # constant via import, so this is really a regression guard
        # against someone redefining it in either file.
        engine_dir = REPO_ROOT / "stacklets" / "backup" / "engines" / "external-disk"
        sys.path.insert(0, str(engine_dir))
        from sync import CANARY_STRING as ENGINE_CANARY
        assert on_install.CANARY_STRING == ENGINE_CANARY


class TestCronCommand:
    def test_invokes_stack_backup_sync(self):
        cmd = on_install._cron_command(Path("/repo"), Path("/data/backup"))
        # The cron command must call the right CLI on the right repo.
        assert "/repo/stack" in cmd
        assert "backup sync" in cmd

    def test_redirects_output_to_cron_log(self):
        # Cron output is invisible by default; the redirect ensures a
        # misbehaving scheduled run leaves a trail the user can inspect.
        cmd = on_install._cron_command(Path("/repo"), Path("/data/backup"))
        assert "/data/backup/logs/cron.log" in cmd
        assert ">>" in cmd
        assert "2>&1" in cmd

    def test_uses_absolute_paths(self):
        # cron's PATH is minimal; relative paths break. Both the binary
        # and the log destination must be absolute.
        cmd = on_install._cron_command(Path("/repo"), Path("/data/backup"))
        for token in cmd.split():
            # Skip the redirect operators and 2>&1
            if token in (">>", "2>&1", "backup", "sync"):
                continue
            assert token.startswith("/"), f"non-absolute token in cron line: {token!r}"
