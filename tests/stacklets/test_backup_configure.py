"""Unit tests for the backup stacklet's on_configure helpers.

Covers the pure-function parts: schedule parsing, filesystem type
classification (via mocked stat), encryption detection (via mocked
diskutil). The interactive ``run(ctx)`` flow needs a full ctx mock and
stdin/stdout, which is integration territory.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "stacklets" / "backup" / "hooks"))

# Need stack.prompt importable for the module to load — add lib/ to path.
sys.path.insert(0, str(REPO_ROOT / "lib"))

import on_configure as configure  # noqa: E402


# ── _parse_time_to_cron ────────────────────────────────────────────────────

class TestParseTimeToCron:
    def test_two_oclock_morning(self):
        assert configure._parse_time_to_cron("02:00") == "0 2 * * *"

    def test_single_digit_hour(self):
        assert configure._parse_time_to_cron("3:30") == "30 3 * * *"

    def test_late_evening(self):
        assert configure._parse_time_to_cron("22:15") == "15 22 * * *"

    def test_midnight(self):
        assert configure._parse_time_to_cron("00:00") == "0 0 * * *"

    def test_whitespace_stripped(self):
        assert configure._parse_time_to_cron("  04:00  ") == "0 4 * * *"

    def test_returns_empty_on_invalid_hour(self):
        assert configure._parse_time_to_cron("25:00") == ""

    def test_returns_empty_on_invalid_minute(self):
        assert configure._parse_time_to_cron("02:60") == ""

    def test_returns_empty_on_garbage(self):
        assert configure._parse_time_to_cron("not a time") == ""

    def test_returns_empty_on_seconds_format(self):
        # We only accept HH:MM, not HH:MM:SS — cron doesn't do seconds.
        assert configure._parse_time_to_cron("02:00:00") == ""


# ── _stat_fs_type (mocked mount output) ────────────────────────────────────

class TestStatFsType:
    """Parses ``mount`` output. NOT ``stat -f %T`` — that returns the
    ls-F file type suffix, not the filesystem type. This was a real
    bug found by E2E; tests now exercise the actual parsing path.
    """

    def _mount_lines(self, *lines):
        return "\n".join(lines) + "\n"

    def test_apfs_extracted_from_mount(self):
        out = self._mount_lines(
            "/dev/disk3s1 on / (apfs, sealed, local, read-only)",
            "/dev/disk5s1 on /Volumes/foo (apfs, local, nodev, nosuid)",
        )
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = out
            assert configure._stat_fs_type(Path("/Volumes/foo")) == "apfs"

    def test_smbfs_extracted(self):
        out = self._mount_lines(
            "//user@server/share on /Volumes/share (smbfs, nodev, nosuid)"
        )
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = out
            assert configure._stat_fs_type(Path("/Volumes/share")) == "smbfs"

    def test_msdos_fat_extracted(self):
        out = self._mount_lines(
            "/dev/disk6s1 on /Volumes/fat (msdos, local, nodev)"
        )
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = out
            assert configure._stat_fs_type(Path("/Volumes/fat")) == "msdos"

    def test_empty_string_when_mount_point_not_found(self):
        out = self._mount_lines(
            "/dev/disk3s1 on / (apfs, sealed)",
        )
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = out
            assert configure._stat_fs_type(Path("/Volumes/absent")) == ""

    def test_partial_path_prefix_does_not_match(self):
        # /Volumes/foo and /Volumes/foobar are distinct mounts — the
        # parser must not match the wrong one.
        out = self._mount_lines(
            "/dev/disk5s1 on /Volumes/foobar (apfs, local)"
        )
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = out
            assert configure._stat_fs_type(Path("/Volumes/foo")) == ""

    def test_empty_on_subprocess_failure(self):
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            assert configure._stat_fs_type(Path("/Volumes/x")) == ""


# ── _is_encrypted (mocked diskutil apfs list) ──────────────────────────────

class TestIsEncrypted:
    def test_true_when_filevault_yes_near_disk_name(self):
        # diskutil apfs list emits the disk's name followed by a few
        # lines of attributes; FileVault: Yes appears within ~6 lines.
        stub = (
            "APFS Container Disk5 ...\n"
            "    APFS Volume Disk Identifier: disk5s1\n"
            "    Name: backup-vault (Case-insensitive)\n"
            "    Mount Point: /Volumes/backup-vault\n"
            "    Capacity Consumed: ...\n"
            "    FileVault: Yes\n"
            "    Encrypted: Yes\n"
        )
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = stub
            assert configure._is_encrypted("backup-vault") is True

    def test_false_when_filevault_no(self):
        stub = (
            "APFS Container ...\n"
            "    Name: backup-vault (Case-insensitive)\n"
            "    FileVault: No\n"
        )
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = stub
            assert configure._is_encrypted("backup-vault") is False

    def test_false_when_disk_not_in_output(self):
        stub = "APFS Container ...\n    Name: some-other-disk\n    FileVault: Yes\n"
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = stub
            # Even though "FileVault: Yes" appears in the output, it's
            # for a different disk — must not match ours.
            assert configure._is_encrypted("backup-vault") is False

    def test_false_on_subprocess_failure(self):
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            assert configure._is_encrypted("backup-vault") is False


# ── _get_volume_uuid (mocked diskutil info) ────────────────────────────────

class TestGetVolumeUuid:
    def test_extracts_uuid_from_diskutil_info(self):
        stub = (
            "   Device / Media Name:      Backup Vault\n"
            "   Volume Name:              backup-vault\n"
            "   Mount Point:              /Volumes/backup-vault\n"
            "   File System Personality:  APFS\n"
            "   Volume UUID:              ABCD1234-5678-90AB-CDEF-1234567890AB\n"
            "   Disk / Partition UUID:    F00D0000-0000-0000-0000-000000000000\n"
        )
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = stub
            assert (
                configure._get_volume_uuid("backup-vault")
                == "ABCD1234-5678-90AB-CDEF-1234567890AB"
            )

    def test_empty_string_when_uuid_missing(self):
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "Volume Name: x\n"  # no UUID line
            assert configure._get_volume_uuid("backup-vault") == ""

    def test_empty_string_on_subprocess_failure(self):
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            assert configure._get_volume_uuid("backup-vault") == ""


# ── _keychain_has_entry (mocked security) ──────────────────────────────────

class TestKeychainHasEntry:
    def test_true_when_security_finds_password(self):
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "the-passphrase\n"
            assert configure._keychain_has_entry("UUID-1234") is True

    def test_false_when_security_fails(self):
        # security exits non-zero when no matching item exists.
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 44  # "specified item not found"
            run.return_value.stdout = ""
            assert configure._keychain_has_entry("UUID-1234") is False

    def test_false_when_password_blank(self):
        # Defensive: if security somehow returns success but empty
        # password, treat as no entry.
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "\n"
            assert configure._keychain_has_entry("UUID-1234") is False


# ── _store_keychain_passphrase (mocked security) ───────────────────────────

class TestStoreKeychainPassphrase:
    def test_returns_true_on_success(self):
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 0
            assert configure._store_keychain_passphrase(
                "UUID-1234", "backup-vault", "secret"
            ) is True

    def test_returns_false_on_failure(self):
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 1
            assert configure._store_keychain_passphrase(
                "UUID-1234", "backup-vault", "secret"
            ) is False

    def test_command_uses_update_flag(self):
        # Using -U means re-running on_configure for an existing disk
        # doesn't fail with "item already exists".
        with patch.object(configure.subprocess, "run") as run:
            run.return_value.returncode = 0
            configure._store_keychain_passphrase("UUID", "disk", "pass")
            args = run.call_args[0][0]
            assert "-U" in args
