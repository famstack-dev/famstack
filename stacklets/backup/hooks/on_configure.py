"""The backup stacklet's setup questions: the disk, its passphrase, the time.

Asked on the first ``stack up backup`` and by ``stack configure backup``,
as forms (ADR-021), so a terminal, a screen and a test walk the same
questions:

1. **The disk** (default ``backup-vault``), offered from the volumes
   mounted now. It must be mounted under ``/Volumes/`` and be APFS (or
   HFS+): those honour the kernel immutability flag the backup relies on.
   A disk that is not mounted, or is not APFS, comes back as the same form
   with the reason on the field. We don't try to format it.
2. **Its passphrase**, only when the disk is APFS-encrypted and the login
   Keychain has no entry for it yet. It is stored in the Keychain, in the
   layout ``diskutil apfs unlockVolume`` reads, so scheduled runs unlock
   the disk unattended; until the admin logs in after a reboot they find
   it locked.
3. **The nightly time**, ``HH:MM``, turned into a 5-field cron expression.

``mount`` is the one source for what is mounted and with which
filesystem. ``apply`` stores the passphrase and writes
``[backup.targets.vault]`` to stack.toml, which configures the stacklet:
after that there is nothing to ask. ``BACKUP_DATA_DIR`` under
``/Volumes/`` is refused before anything is asked, so the framework's
destroy cleanup can never reach external storage.

The FDA grant and cron install happen in on_install, which runs after this.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from stack.forms import Form

from .._config import read_target, write_target


TARGET_NAME = "vault"
DEFAULT_DISK = "backup-vault"
DEFAULT_TIME = "02:00"

# Filesystems that honour the BSD immutability flag (`uchg`).
IMMUTABLE_FILESYSTEMS = ("apfs", "hfs")


# ── The questions ──────────────────────────────────────────────────────────

def questions(ctx, answers):
    if read_target(_stack_toml(ctx), TARGET_NAME) is not None:
        return None
    _refuse_backup_data_dir_on_volumes(ctx)

    volumes = _mounted_volumes()
    disk = _disk_form(volumes)
    if open_form := disk.pending(answers):
        return open_form
    name = answers["disk"]
    if problem := _disk_problem(name, volumes):
        return disk.with_errors(disk=problem)

    if _is_encrypted(name):
        volume_uuid = _get_volume_uuid(name)
        if not volume_uuid:
            return disk.with_errors(disk=f"Couldn't determine the Volume UUID of {name}.")
        if not _keychain_has_entry(volume_uuid) and (
                open_form := _passphrase_form(name).pending(answers)):
            return open_form

    return SCHEDULE.pending(answers)


def apply(ctx, answers):
    name = answers["disk"]
    if "passphrase" in answers:
        volume_uuid = _get_volume_uuid(name)
        if not _store_keychain_passphrase(volume_uuid, name, answers["passphrase"]):
            raise RuntimeError("Failed to store passphrase in Keychain")
        ctx.step(f"Passphrase stored in Keychain (Volume UUID {volume_uuid})")

    schedule = _parse_time_to_cron(answers["time"])
    write_target(_stack_toml(ctx), TARGET_NAME, {
        "engine": "external-disk",
        "disk": name,
        "schedule": schedule,
    })
    ctx.step(f"Target '{TARGET_NAME}' written to stack.toml: {name}, daily at "
             f"{answers['time']} (cron: {schedule})")
    ctx.step("Next: on_install installs the cron entry and walks you through "
             "granting Full Disk Access to /usr/sbin/cron.")


def _stack_toml(ctx) -> Path:
    return Path(ctx.stack.instance_dir) / "stack.toml"


# ── The forms ──────────────────────────────────────────────────────────────

def _disk_form(volumes: dict[str, str]) -> Form:
    """The disk, with the volumes mounted now as suggestions."""
    mounted = ", ".join(f"{name} ({fs})" for name, fs in volumes.items()) or "none"
    return Form(
        id="backup.disk",
        title="Backup disk",
        text=(
            "Photos and documents are copied to the disk and locked with the "
            "kernel-level immutability flag: once written, they can't be modified "
            "or deleted, even by root.\n\n"
            "Plug in your external disk (USB or Thunderbolt). It must be "
            "APFS-formatted and currently mounted under /Volumes/."),
        schema={
            "type": "object",
            "properties": {
                "disk": {
                    "type": "string",
                    "title": "Disk volume name",
                    "description": f"Mounted now: {mounted}.",
                    "default": DEFAULT_DISK,
                    "examples": list(volumes),
                    "minLength": 1,
                },
            },
            "required": ["disk"],
        },
    )


def _passphrase_form(disk_name: str) -> Form:
    return Form(
        id="backup.passphrase",
        title="Backup disk encryption",
        text=(
            f"Disk '{disk_name}' is APFS-encrypted. Encryption is supported but "
            "adds two operational costs:\n"
            "  1. The passphrase must live in your macOS Keychain.\n"
            "  2. After a reboot, scheduled syncs only find the disk once\n"
            "     you've logged in (Keychain unlocks at login).\n\n"
            "We'll store the passphrase in your login Keychain now so the backup "
            "script can unlock the disk unattended."),
        schema={
            "type": "object",
            "properties": {
                "passphrase": {
                    "type": "string",
                    "title": "Disk passphrase",
                    "writeOnly": True,
                    "minLength": 1,
                },
            },
            "required": ["passphrase"],
        },
    )


SCHEDULE = Form(
    id="backup.schedule",
    title="Backup schedule",
    text="Pick a nightly time. 2 AM avoids active hours for most households.",
    schema={
        "type": "object",
        "properties": {
            "time": {
                "type": "string",
                "title": "Time (HH:MM)",
                "format": "time",
                "default": DEFAULT_TIME,
            },
        },
        "required": ["time"],
    },
)


# ── Checks against the Mac ─────────────────────────────────────────────────

def _disk_problem(name: str, volumes: dict[str, str]) -> str:
    """Why the volume `name` cannot hold the backup, or "".

    Non-APFS filesystems are refused here, not later in the engine. The
    engine's probe would still catch SMB/exFAT, but failing at configure
    time means we never write a target config that's guaranteed to fail
    at first sync.
    """
    if name not in volumes:
        return (f"Volume '{name}' is not mounted at /Volumes/{name}. "
                "Check the disk is plugged in and unlocked, then try again.")
    if volumes[name] not in IMMUTABLE_FILESYSTEMS:
        return (f"Filesystem {volumes[name]!r} on /Volumes/{name} doesn't honor BSD "
                "immutability flags. Reformat as APFS (Disk Utility, Erase) or use a "
                "different disk.")
    return ""


def _mounted_volumes() -> dict[str, str]:
    """The volumes mounted under /Volumes/, by name, with their filesystem
    type, parsed from ``mount`` output.

    Mirrors the engine's ``_stat_fs_type``: see that function's docstring
    for why ``mount`` parsing rather than ``stat -f %T``. Kept in sync by
    hand because the engine is standalone-runnable and shouldn't depend on
    hook code. A mount below a volume (``/Volumes/a/b``) is not a volume.
    """
    result = subprocess.run(["mount"], capture_output=True, text=True)
    if result.returncode != 0:
        return {}
    volumes = {}
    for line in result.stdout.splitlines():
        _device, on, rest = line.partition(" on /Volumes/")
        attrs_at = rest.rfind(" (")
        if not on or attrs_at <= 0 or not rest.endswith(")"):
            continue
        name = rest[:attrs_at]
        if "/" not in name:
            volumes[name] = rest[attrs_at + 2:-1].split(",")[0].strip().lower()
    return volumes


def _is_encrypted(disk_name: str) -> bool:
    result = subprocess.run(
        ["diskutil", "apfs", "list"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False
    lines = result.stdout.splitlines()
    for i, line in enumerate(lines):
        if disk_name in line:
            for follow in lines[i:i + 6]:
                if "FileVault:" in follow and "Yes" in follow:
                    return True
            return False
    return False


def _get_volume_uuid(disk_name: str) -> str:
    result = subprocess.run(
        ["diskutil", "info", disk_name],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        if "Volume UUID" in line:
            return line.rsplit(":", 1)[1].strip()
    return ""


def _keychain_has_entry(volume_uuid: str) -> bool:
    result = subprocess.run(
        ["security", "find-generic-password", "-a", volume_uuid, "-w"],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _store_keychain_passphrase(volume_uuid: str, disk_name: str, passphrase: str) -> bool:
    """``security add-generic-password`` with the layout that
    ``diskutil apfs unlockVolume`` recognizes. Its arguments carry the
    passphrase, so a failure says only that it failed."""
    result = subprocess.run(
        [
            "security", "add-generic-password",
            "-a", volume_uuid,
            "-s", volume_uuid,
            "-D", "APFS Volume Password",
            "-l", disk_name,
            "-w", passphrase,
            "-U",  # Update if entry already exists
        ],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _parse_time_to_cron(time_str: str) -> str:
    """Convert ``HH:MM`` to a 5-field daily cron expression.
    Returns empty string on invalid input."""
    match = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", time_str)
    if not match:
        return ""
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return ""
    return f"{minute} {hour} * * *"


def _refuse_backup_data_dir_on_volumes(ctx) -> None:
    """The framework's destroy cleanup deletes data_dir/<id>/ recursively.
    If we ever pointed BACKUP_DATA_DIR at the vault, destroy would wipe
    the vault. The default ``{data_dir}/backup`` is safe; refuse loudly
    if someone overrode it to a /Volumes/ path."""
    env_defaults = ctx.stack.config.get("env", {}).get("defaults", {})
    backup_dir_template = env_defaults.get("BACKUP_DATA_DIR", "")
    if backup_dir_template.startswith("/Volumes/"):
        raise RuntimeError(
            "BACKUP_DATA_DIR points at /Volumes/, refused for safety. "
            "Logs, canary, and result state must live on the internal SSD "
            "so the framework's destroy cleanup can't reach external storage."
        )
