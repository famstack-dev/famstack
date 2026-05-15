"""on_configure — interactive setup for the backup stacklet.

Runs once on first ``stack up backup``. Idempotent — if the target
config already exists in stack.toml, we treat that as "already
configured" and skip the wizard.

Walks the user through, in order:

1. Picking the vault disk (default name ``backup-vault``). The disk
   must already be attached and mounted; we don't try to format it.
2. Detecting whether the disk is APFS-encrypted. If so, walk through
   the macOS Keychain setup so future runs can unlock unattended. The
   user is warned that scheduled syncs after a reboot may need a manual
   unlock until they next log in.
3. Picking the nightly schedule (``HH:MM`` form, converted to a 5-field
   cron expression).
4. Refusing to set ``BACKUP_DATA_DIR`` under ``/Volumes/`` (a defensive
   measure against the framework's destroy cleanup reaching external
   storage).
5. Writing ``[backup.targets.vault]`` to stack.toml via the narrow
   ``_config.write_target`` helper. The framework picks it up on the
   next read.

The .app/FDA/cron install happens in on_install, which runs after this.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


_BACKUP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKUP_DIR))

from _config import read_target, write_target  # noqa: E402

from stack.prompt import (  # noqa: E402
    ask, bold, confirm, dim, done, nl, out, section, warn,
)


TARGET_NAME = "vault"
DEFAULT_DISK = "backup-vault"
DEFAULT_TIME = "02:00"


# ── Hook entry ─────────────────────────────────────────────────────────────

def run(ctx):
    instance_dir = Path(ctx.stack.instance_dir)
    stack_toml = instance_dir / "stack.toml"

    if read_target(stack_toml, TARGET_NAME) is not None:
        ctx.step(f"Target '{TARGET_NAME}' already configured in stack.toml")
        return

    if not sys.stdin.isatty():
        raise RuntimeError(
            "Backup configuration requires interactive input. "
            "Add [backup.targets.vault] to stack.toml or run "
            "'stack up backup' from a terminal."
        )

    _explain()
    disk_name = _ask_disk_name()
    _verify_mounted_and_apfs(disk_name)
    _handle_encryption(disk_name)
    schedule = _ask_schedule()

    _validate_backup_data_dir(ctx)

    write_target(stack_toml, TARGET_NAME, {
        "engine": "external-disk",
        "disk": disk_name,
        "schedule": schedule,
    })

    nl()
    done(f"Target '{TARGET_NAME}' written to stack.toml")
    dim("  Next: on_install will install the FDA-granted .app wrapper and the cron entry.")
    nl()


# ── Steps ──────────────────────────────────────────────────────────────────

def _explain() -> None:
    section("Backup", "Append-only sync of stacklet data to an attached APFS disk")
    out("Photos and documents are copied to the disk and locked with")
    out("the kernel-level immutability flag — once written, they can't")
    out("be modified or deleted, even by root.")
    nl()


def _ask_disk_name() -> str:
    bold("Step 1 — Backup disk")
    out("Plug in your external disk (USB or Thunderbolt). It must be")
    out("APFS-formatted and currently mounted under /Volumes/.")
    nl()

    while True:
        name = ask("Disk volume name", default=DEFAULT_DISK)
        if not name:
            raise RuntimeError("No disk name entered")
        mount_point = Path("/Volumes") / name
        if mount_point.is_dir():
            done(f"Found {name} at {mount_point}")
            return name
        warn(f"Volume '{name}' is not mounted at /Volumes/{name}")
        dim("  Check the disk is plugged in and unlocked, then try again.")
        if not confirm("Try a different name?", default=True):
            raise RuntimeError("Backup disk not mounted")


def _verify_mounted_and_apfs(disk_name: str) -> None:
    """Refuse non-APFS filesystems here, not later in the engine. The
    engine's probe would still catch SMB/exFAT, but failing at
    configure time means we never write a target config that's
    guaranteed to fail at first sync.
    """
    bold("Step 2 — Filesystem check")
    mount_point = Path("/Volumes") / disk_name
    fs_type = _stat_fs_type(mount_point)
    if fs_type in ("apfs", "hfs"):
        done(f"Filesystem is {fs_type} — kernel immutability available")
        return
    raise RuntimeError(
        f"Filesystem {fs_type or 'unknown'!r} on /Volumes/{disk_name} doesn't honor "
        "BSD immutability flags. Reformat as APFS (Disk Utility → Erase) "
        "or use a different disk."
    )


def _stat_fs_type(mount_point: Path) -> str:
    """Filesystem type at a mount point, parsed from ``mount`` output.

    Mirrors the engine's ``_stat_fs_type`` — see that function's
    docstring for why ``mount`` parsing rather than ``stat -f %T``.
    Kept in sync by hand because the engine is standalone-runnable
    and shouldn't depend on hook code.
    """
    result = subprocess.run(
        ["mount"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return ""
    target = str(mount_point)
    for line in result.stdout.splitlines():
        head_split = line.split(" on ", 1)
        if len(head_split) != 2:
            continue
        rest = head_split[1]
        if not rest.startswith(f"{target} ("):
            continue
        paren_start = rest.find("(") + 1
        paren_end = rest.rfind(")")
        if paren_end <= paren_start:
            continue
        attrs = rest[paren_start:paren_end]
        return attrs.split(",")[0].strip().lower()
    return ""


def _handle_encryption(disk_name: str) -> None:
    bold("Step 3 — Encryption")
    if not _is_encrypted(disk_name):
        out("Disk is plain APFS (not encrypted).")
        dim("  Encryption protects against physical drive theft only.")
        dim("  The uchg + offline-eject layers handle the ransomware threat")
        dim("  model on their own. Plain APFS is the recommended default.")
        nl()
        return

    warn(f"Disk '{disk_name}' is APFS-encrypted.")
    dim("  Encryption is supported but adds two operational costs:")
    dim("    1. The passphrase must live in your macOS Keychain.")
    dim("    2. After a reboot, scheduled syncs only find the disk once")
    dim("       you've logged in (Keychain unlocks at login).")
    nl()

    volume_uuid = _get_volume_uuid(disk_name)
    if not volume_uuid:
        raise RuntimeError(f"Couldn't determine Volume UUID for {disk_name}")

    if _keychain_has_entry(volume_uuid):
        done(f"Keychain entry already present for Volume UUID {volume_uuid}")
        nl()
        return

    out("We'll store the passphrase in your login Keychain now so the")
    out("backup script can unlock the disk unattended.")
    nl()
    passphrase = _read_passphrase("Disk passphrase")
    if not passphrase:
        raise RuntimeError("No passphrase entered")

    if not _store_keychain_passphrase(volume_uuid, disk_name, passphrase):
        raise RuntimeError("Failed to store passphrase in Keychain")

    done(f"Passphrase stored in Keychain (Volume UUID {volume_uuid})")
    nl()


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


def _read_passphrase(prompt: str) -> str:
    """Read a passphrase without echo. Falls back to plain input if
    getpass isn't usable (rare, but the framework runs in unusual
    environments)."""
    try:
        import getpass
        return getpass.getpass(f"  ▸ {prompt}: ")
    except Exception:
        return ask(prompt, default="") or ""


def _store_keychain_passphrase(volume_uuid: str, disk_name: str, passphrase: str) -> bool:
    """``security add-generic-password`` with the layout that
    ``diskutil apfs unlockVolume`` recognizes."""
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


def _ask_schedule() -> str:
    bold("Step 4 — Schedule")
    out("Pick a nightly time. 2 AM avoids active hours for most households.")
    nl()

    while True:
        time_str = ask("Time (HH:MM)", default=DEFAULT_TIME)
        if not time_str:
            raise RuntimeError("No time entered")
        cron = _parse_time_to_cron(time_str)
        if cron:
            done(f"Scheduled daily at {time_str}  (cron: {cron})")
            nl()
            return cron
        warn(f"'{time_str}' is not a valid HH:MM time")
        dim("  Examples: 02:00, 03:30, 22:15")


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


def _validate_backup_data_dir(ctx) -> None:
    """The framework's destroy cleanup deletes data_dir/<id>/ recursively.
    If we ever pointed BACKUP_DATA_DIR at the vault, destroy would wipe
    the vault. The default ``{data_dir}/backup`` is safe; warn loudly
    if someone overrode it to a /Volumes/ path."""
    env_defaults = ctx.stack.config.get("env", {}).get("defaults", {})
    backup_dir_template = env_defaults.get("BACKUP_DATA_DIR", "")
    if backup_dir_template.startswith("/Volumes/"):
        raise RuntimeError(
            "BACKUP_DATA_DIR points at /Volumes/ — refused for safety. "
            "Logs, canary, and result state must live on the internal SSD "
            "so the framework's destroy cleanup can't reach external storage."
        )
