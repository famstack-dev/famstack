"""Unit tests for the backup stacklet's cron helper.

The crontab command is mocked everywhere — these tests assert the
behavior the helper promises (idempotent install, target-scoped
removal, loud failure on edit errors) without touching the real
crontab.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "stacklets" / "backup"))

import _cron as cron  # noqa: E402


# ── Helpers ────────────────────────────────────────────────────────────────

class _FakeCron:
    """Test double for the crontab subprocess. Holds in-memory state
    and serves both ``crontab -l`` and ``crontab -`` calls."""

    def __init__(self, initial: str = ""):
        self.content = initial
        self.write_calls = 0
        self.next_write_should_fail = False

    def run(self, argv, **kw):
        result = MagicMock()
        if argv == ["crontab", "-l"]:
            if self.content:
                result.returncode = 0
                result.stdout = self.content
            else:
                # Match real crontab behavior: exit non-zero when no
                # crontab is installed.
                result.returncode = 1
                result.stdout = ""
            return result
        if argv == ["crontab", "-"]:
            if self.next_write_should_fail:
                result.returncode = 1
                result.stderr = "crontab: locked"
                return result
            self.content = kw["input"]
            self.write_calls += 1
            result.returncode = 0
            result.stderr = ""
            return result
        raise RuntimeError(f"unexpected subprocess call: {argv}")


def _patched_subprocess(fake: _FakeCron):
    """Patch _cron.subprocess.run to dispatch through the fake."""
    return patch.object(cron.subprocess, "run", side_effect=fake.run)


# ── install_entry ──────────────────────────────────────────────────────────

class TestInstallEntry:
    def test_installs_into_empty_crontab(self):
        fake = _FakeCron()
        with _patched_subprocess(fake):
            changed = cron.install_entry("0 2 * * *", "open /a/b.app", "vault")
        assert changed is True
        assert "0 2 * * * open /a/b.app" in fake.content
        assert "famstack-backup-vault" in fake.content

    def test_appends_to_existing_unrelated_entries(self):
        fake = _FakeCron("0 5 * * * /some/other/job\n")
        with _patched_subprocess(fake):
            cron.install_entry("0 2 * * *", "open /a.app", "vault")
        # The original unrelated entry survives.
        assert "/some/other/job" in fake.content
        # Ours got added.
        assert "famstack-backup-vault" in fake.content

    def test_replaces_existing_entry_for_same_target(self):
        fake = _FakeCron(
            "0 2 * * * open /old.app  # famstack-backup-vault\n"
        )
        with _patched_subprocess(fake):
            cron.install_entry("0 3 * * *", "open /new.app", "vault")

        # Old line gone, new line present.
        assert "/old.app" not in fake.content
        assert "/new.app" in fake.content
        # Only one famstack-backup-vault line.
        assert fake.content.count("famstack-backup-vault") == 1

    def test_idempotent_reinstall_returns_false(self):
        # Installing the exact same entry twice should be a no-op.
        fake = _FakeCron()
        with _patched_subprocess(fake):
            first = cron.install_entry("0 2 * * *", "open /a.app", "vault")
            second = cron.install_entry("0 2 * * *", "open /a.app", "vault")
        assert first is True
        assert second is False
        # The second call didn't issue a write.
        assert fake.write_calls == 1

    def test_two_targets_coexist(self):
        # Vault and offsite are different targets — both entries must
        # survive each other's install.
        fake = _FakeCron()
        with _patched_subprocess(fake):
            cron.install_entry("0 2 * * *", "open /vault.app", "vault")
            cron.install_entry("0 4 * * 0", "open /offsite.app", "offsite")
        assert "famstack-backup-vault" in fake.content
        assert "famstack-backup-offsite" in fake.content
        assert "vault.app" in fake.content
        assert "offsite.app" in fake.content

    def test_raises_when_crontab_write_fails(self):
        fake = _FakeCron()
        fake.next_write_should_fail = True
        with _patched_subprocess(fake):
            with pytest.raises(RuntimeError, match="crontab edit failed"):
                cron.install_entry("0 2 * * *", "open /a.app", "vault")


# ── remove_entry ───────────────────────────────────────────────────────────

class TestRemoveEntry:
    def test_removes_matching_entry(self):
        fake = _FakeCron(
            "0 2 * * * open /a.app  # famstack-backup-vault\n"
        )
        with _patched_subprocess(fake):
            removed = cron.remove_entry("vault")
        assert removed is True
        assert "famstack-backup-vault" not in fake.content

    def test_returns_false_when_no_entry_present(self):
        # Empty crontab, nothing to remove — must not fail.
        fake = _FakeCron()
        with _patched_subprocess(fake):
            removed = cron.remove_entry("vault")
        assert removed is False
        assert fake.write_calls == 0  # didn't bother to write

    def test_leaves_unrelated_entries_intact(self):
        fake = _FakeCron(
            "0 5 * * * /some/other/job\n"
            "0 2 * * * open /a.app  # famstack-backup-vault\n"
        )
        with _patched_subprocess(fake):
            cron.remove_entry("vault")
        assert "/some/other/job" in fake.content
        assert "famstack-backup-vault" not in fake.content

    def test_removing_vault_does_not_touch_offsite(self):
        # Target-scoped marker means destroying one target leaves the
        # other's entry untouched.
        fake = _FakeCron(
            "0 2 * * * open /vault.app  # famstack-backup-vault\n"
            "0 4 * * 0 open /offsite.app  # famstack-backup-offsite\n"
        )
        with _patched_subprocess(fake):
            cron.remove_entry("vault")
        assert "famstack-backup-vault" not in fake.content
        assert "famstack-backup-offsite" in fake.content
        assert "/offsite.app" in fake.content

    def test_idempotent_double_removal(self):
        # Belt-and-suspenders: destroy lifecycle calls on_stop AND
        # on_destroy; both call remove_entry. Second call must be a
        # no-op, not a failure.
        fake = _FakeCron(
            "0 2 * * * open /a.app  # famstack-backup-vault\n"
        )
        with _patched_subprocess(fake):
            first = cron.remove_entry("vault")
            second = cron.remove_entry("vault")
        assert first is True
        assert second is False

    def test_empty_crontab_after_removal_is_written_empty(self):
        # If our entry was the only one, the crontab should end up
        # empty, not deleted. (Real-world: this avoids surprise if the
        # user's crontab had only our entry.)
        fake = _FakeCron(
            "0 2 * * * open /a.app  # famstack-backup-vault\n"
        )
        with _patched_subprocess(fake):
            cron.remove_entry("vault")
        assert fake.content == ""


# ── is_installed ──────────────────────────────────────────────────────────

class TestIsInstalled:
    def test_true_when_marker_present(self):
        fake = _FakeCron(
            "0 2 * * * open /a.app  # famstack-backup-vault\n"
        )
        with _patched_subprocess(fake):
            assert cron.is_installed("vault") is True

    def test_false_when_marker_absent(self):
        fake = _FakeCron("0 5 * * * /some/other/job\n")
        with _patched_subprocess(fake):
            assert cron.is_installed("vault") is False

    def test_false_on_empty_crontab(self):
        fake = _FakeCron()
        with _patched_subprocess(fake):
            assert cron.is_installed("vault") is False

    def test_scoped_to_target_name(self):
        # vault marker present, but caller asks about offsite — answer
        # must be False.
        fake = _FakeCron(
            "0 2 * * * open /a.app  # famstack-backup-vault\n"
        )
        with _patched_subprocess(fake):
            assert cron.is_installed("offsite") is False


# ── remove_all_entries ────────────────────────────────────────────────────

class TestRemoveAllEntries:
    def test_removes_every_famstack_backup_entry(self):
        fake = _FakeCron(
            "0 2 * * * open /v.app  # famstack-backup-vault\n"
            "0 4 * * 0 open /o.app  # famstack-backup-offsite\n"
            "0 5 * * * /unrelated/job\n"
        )
        with _patched_subprocess(fake):
            removed = cron.remove_all_entries()
        assert removed == 2
        assert "famstack-backup" not in fake.content
        assert "/unrelated/job" in fake.content

    def test_returns_zero_when_nothing_to_remove(self):
        fake = _FakeCron("0 5 * * * /unrelated/job\n")
        with _patched_subprocess(fake):
            removed = cron.remove_all_entries()
        assert removed == 0
        # No write — there was nothing to do.
        assert fake.write_calls == 0

    def test_handles_empty_crontab(self):
        fake = _FakeCron()
        with _patched_subprocess(fake):
            removed = cron.remove_all_entries()
        assert removed == 0
