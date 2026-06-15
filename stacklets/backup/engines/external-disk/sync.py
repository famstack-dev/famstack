#!/usr/bin/env python3
"""external-disk engine — append-only backup of stacklet data.

Ported from `family-server/backup/vault-sync.sh`. The control flow, exit
codes, and append-only contract are preserved; the implementation is
Python so it slots cleanly into a framework where every other layer
(hooks, CLI, lib) is also Python.

Three adaptations from the bash original:

1. Sources come from the ``$SOURCES`` env var (pipe-delimited records)
   instead of a hardcoded list. The orchestrator (``cli/sync.py``) walks
   enabled stacklets, gathers their ``[[backup.archive]]`` declarations,
   and passes them in.

2. The filesystem capability check is a function call
   (:func:`probe_filesystem`) rather than a separate ``probe.sh``.
   Refuses non-APFS/non-HFS+ targets so the kernel-immutability guarantee
   can't silently degrade on SMB/NFS/exFAT.

3. No Matrix notification. The engine appends one JSON object per run
   to ``$BACKUP_DATA_DIR/logs/history.jsonl``; the caller reads the
   latest entry and posts via ``stacker-bot``. Messaging is a separate
   concern.

Input (environment):

==================  ==========================================================
``BACKUP_DATA_DIR`` Required. The backup stacklet's own state directory
                    (canary file, audit log, run history). NOT the
                    source data being backed up, NOT
                    the target vault disk. Follows the framework
                    convention {STACKLET}_DATA_DIR (cf. PAPERLESS_DATA_DIR).
                    Must NOT be under ``/Volumes/`` — the script refuses,
                    because that's where the framework's destroy cleanup
                    would not reach.
``VAULT_DISK``      Required. APFS volume name (e.g. ``backup-vault``). The
                    mount point is ``/Volumes/<name>``.
``SOURCES``         Required. Newline-separated records, pipe-delimited::

                        <id>|<display>|<src_path>|<vault_subdir>|<min_files>
``TZ``              Optional. Affects log timestamps.
==================  ==========================================================

Output:

* stdout — human-readable progress.
* stderr — warnings and errors.
* ``$BACKUP_DATA_DIR/logs/history.jsonl`` — one JSON object per run,
  append-only. Read the last good line for the latest run's outcome.
* ``$BACKUP_DATA_DIR/logs/sync.log`` — human-readable audit log.
* Exit code: 0 on success, 1 on hard failure. A run is always
  appended to history, so the caller can distinguish "engine reported
  a failure" from "engine crashed before it could report."
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


# ── Constants ──────────────────────────────────────────────────────────────

CANARY_STRING = "famstack-backup-canary-do-not-delete"

# Filesystem types that honor BSD ``uchg`` flags. Anything else is refused.
APPLE_FILESYSTEMS = frozenset({"apfs", "hfs"})

# Filesystem types we recognize and refuse with a tailored message.
NETWORK_FILESYSTEMS = frozenset({"smbfs", "nfs", "afpfs"})
REMOVABLE_FILESYSTEMS = frozenset({"msdos", "exfat", "ntfs"})

# rsync exit codes that aren't real failures for incremental backups:
# 0 = success, 23 = partial transfer (vanished files during sync),
# 24 = source files vanished. All acceptable in our context.
RSYNC_OK_CODES = frozenset({0, 23, 24})


# ── Domain types ───────────────────────────────────────────────────────────

@dataclass
class Source:
    """One ``[[backup.archive]]`` declaration, as seen by the engine."""

    id: str            # global identity, e.g. "photos/library"
    display: str       # human-readable label, e.g. "Photos"
    src_path: Path     # absolute path on the internal SSD
    vault_subdir: str  # relative path under /Volumes/<vault>/
    min_files: int     # coarse ransomware guard threshold


@dataclass
class SourceResult:
    """Per-source outcome after the sync."""

    id: str
    display: str
    status: str        # "ok" | "FAILED" | "skipped"
    total_files: int   # files on vault after this run
    new_files: int     # files added this run


@dataclass
class SyncResult:
    """The structured per-run result. One of these is serialized to JSON
    and appended as a single line to ``history.jsonl``."""

    success: bool = True
    dry_run: bool = False
    failure_reason: Optional[str] = None
    duration_seconds: int = 0
    started_at: str = ""
    ended_at: str = ""
    run_context: str = "unknown"  # "Terminal" | "cron" | "launchd"
    run_user: str = ""
    vault_disk: str = ""
    vault_state: str = "unknown"  # "mounted" | "ejected" | "not_connected"
    vault_size: str = "unknown"
    sources: List[SourceResult] = field(default_factory=list)


class SyncAborted(Exception):
    """A pipeline step refused to continue (canary, preflight, mount, probe).
    The caller catches and records as ``failure_reason``."""


class DriveNotConnected(SyncAborted):
    """Specifically: diskutil cannot see the volume at all. Surfaced as
    a distinct ``vault_state`` so the caller can render a useful
    message ("plug your backup disk in") instead of a generic failure.
    """


# ── Output helpers ─────────────────────────────────────────────────────────

# ANSI colors only when stdout is a TTY. Cron output is a file; escape
# sequences would clutter it.
_USE_COLOR = sys.stdout.isatty()

GREEN = "\033[0;32m" if _USE_COLOR else ""
RED = "\033[0;31m" if _USE_COLOR else ""
YELLOW = "\033[0;33m" if _USE_COLOR else ""
BOLD = "\033[1m" if _USE_COLOR else ""
NC = "\033[0m" if _USE_COLOR else ""


def info(msg: str) -> None:
    print(f"  {GREEN}→{NC} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}⚠{NC} {msg}")


def error(msg: str) -> None:
    print(f"  {RED}✗{NC} {msg}", file=sys.stderr)


def header(msg: str) -> None:
    print()
    print(f"{BOLD}{msg}{NC}")


def append_log(log_path: Path, message: str) -> None:
    """Append a timestamped line to the audit log. Best-effort: if the
    log directory can't be created, the message is dropped. We never
    want a logging failure to take down the sync."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log_path.open("a") as f:
            f.write(f"[{ts}] {message}\n")
    except OSError:
        pass


# ── Source parsing ─────────────────────────────────────────────────────────

def parse_sources(sources_env: str) -> List[Source]:
    """Parse the ``$SOURCES`` env var into structured records.

    Records are newline-separated, fields pipe-delimited::

        <id>|<display>|<src_path>|<vault_subdir>|<min_files>

    Pipe over colon because paths can (rarely) contain colons on macOS
    but never pipes. Empty input or malformed records raise
    :class:`SyncAborted` — we'd rather fail loudly than silently skip
    a misconfigured source.
    """
    records: List[Source] = []
    for raw in sources_env.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) != 5:
            raise SyncAborted(
                f"Malformed source record: {line!r} "
                f"(expected 5 pipe-delimited fields, got {len(parts)})"
            )
        id_, display, src_path, vault_subdir, min_files = parts
        try:
            min_files_int = int(min_files)
        except ValueError:
            raise SyncAborted(f"min_files must be an integer in {line!r}")
        records.append(Source(
            id=id_,
            display=display,
            src_path=Path(src_path),
            vault_subdir=vault_subdir,
            min_files=min_files_int,
        ))
    if not records:
        raise SyncAborted("No sources provided — $SOURCES is empty.")
    return records


# ── Number formatting ──────────────────────────────────────────────────────

def format_number(n: int) -> str:
    """Format an integer with dot thousands separators (e.g. ``48.293``).

    Why dot, not comma: a comma triggers phone-number linkification in
    some chat clients (Element among them); a dot doesn't. Reads
    European but is unambiguous everywhere.
    """
    s = str(n)
    if len(s) <= 3:
        return s
    parts: List[str] = []
    while len(s) > 3:
        parts.insert(0, s[-3:])
        s = s[:-3]
    parts.insert(0, s)
    return ".".join(parts)


def count_files(path: Path) -> int:
    """Count regular files under a directory, recursively. Returns 0 if
    the path doesn't exist or isn't a directory — the caller decides
    whether that constitutes a failure."""
    if not path.is_dir():
        return 0
    return sum(1 for p in path.rglob("*") if p.is_file())


# ── Canary (ransomware tripwire) ───────────────────────────────────────────

def verify_canary(canary_file: Path) -> None:
    """Verify the canary's content. The engine never creates it.

    The canary is a small file on the internal SSD with known content.
    If ransomware has encrypted any part of the data hierarchy, the
    content won't match and we abort before touching the vault.

    Lifecycle: ``on_install`` plants the canary as part of setup;
    framework destroy cleanup removes it with the rest of
    ``BACKUP_DATA_DIR``. The engine only verifies — never creates,
    never repairs. Missing canary means either the stacklet was never
    installed or the tripwire has been deleted; both are refusals.
    """
    header("Canary check")

    if not canary_file.exists():
        error("Canary file is missing.")
        error(f"Expected at: {canary_file}")
        error("If this is a fresh install, run 'stack up backup' first.")
        error("If the stacklet IS installed, the tripwire has been tampered with.")
        raise SyncAborted(
            "Canary file is missing — backup not installed or tripwire deleted"
        )

    content = canary_file.read_text().strip()
    if content != CANARY_STRING:
        error("Canary file modified or corrupted!")
        error(f"Expected: {CANARY_STRING!r}")
        error(f"Got:      {content!r}")
        error("Possible ransomware or data corruption. Aborting.")
        raise SyncAborted(
            "Canary check failed — possible ransomware or data corruption"
        )

    info("Canary verified (internal SSD looks healthy)")


# ── Preflight ──────────────────────────────────────────────────────────────

def preflight_check_sources(sources: List[Source]) -> None:
    """Each source must exist and contain at least ``min_files`` entries.

    The canary catches "every file got encrypted in place"; this catches
    "the directory got ``rm -rf``'d." Together they're a layered smoke
    test that refuses to propagate a broken source to the vault.
    """
    header("Preflight checks")

    failures: List[str] = []
    for src in sources:
        if not src.src_path.is_dir():
            error(f"{src.display}: source directory not found ({src.src_path})")
            failures.append(src.display)
            continue
        count = count_files(src.src_path)
        if count < src.min_files:
            error(
                f"{src.display}: only {count} files "
                f"(minimum: {src.min_files}) — refusing to sync"
            )
            failures.append(src.display)
        else:
            info(
                f"{src.display}: {format_number(count)} files "
                f"(minimum: {src.min_files}) — ok"
            )

    if failures:
        raise SyncAborted(
            "Preflight failed — source directories missing or too few files"
        )


# ── Mount vault ────────────────────────────────────────────────────────────

@dataclass
class MountState:
    """Outcome of :func:`mount_vault`. Tracked so the caller can report
    whether the disk was already mounted vs. mounted by this run."""

    was_already_mounted: bool


def mount_vault(vault_disk: str, mount_point: Path, dry_run: bool) -> MountState:
    """Bring the vault to a mounted state.

    Plain APFS volumes auto-mount on physical connection — macOS does
    this for us, so the common case is a no-op. Encrypted APFS volumes
    need explicit unlock: we fetch the passphrase from the macOS
    Keychain by Volume UUID and pipe it to ``diskutil -stdinpassphrase``.
    diskutil's built-in keychain lookup is unreliable on macOS 26+;
    explicit retrieval works.

    "Drive not connected" is raised as :class:`DriveNotConnected` (a
    subclass of :class:`SyncAborted`) so the caller can mark
    ``vault_state="not_connected"`` rather than a generic failure.
    """
    header("Mounting backup disk")

    if mount_point.is_dir():
        info(f"Backup disk already mounted at {mount_point}")
        return MountState(was_already_mounted=True)

    drive_detected = _diskutil_info_exists(vault_disk)

    if dry_run:
        if drive_detected:
            info("[DRY RUN] Would mount backup disk (drive detected)")
            return MountState(was_already_mounted=False)
        error(f"[DRY RUN] Backup disk not detected — volume {vault_disk!r} not found")
        error("Is the drive enclosure powered on and connected? A real run would fail here.")
        raise DriveNotConnected("Backup disk not connected")

    if not drive_detected:
        error(f"Backup disk not detected — volume {vault_disk!r} not found in diskutil")
        error("Is the drive enclosure powered on and connected via USB?")
        raise DriveNotConnected("Backup disk not connected")

    if _is_filevault_encrypted(vault_disk):
        info(f"Unlocking encrypted volume {vault_disk}...")
        _unlock_encrypted_volume(vault_disk)
    else:
        info(f"Mounting {vault_disk}...")
        _mount_plain_volume(vault_disk)

    info(f"Backup disk mounted at {mount_point}")
    return MountState(was_already_mounted=False)


def _diskutil_info_exists(vault_disk: str) -> bool:
    """True if diskutil can see the volume (even when its container is
    locked but powered on)."""
    result = subprocess.run(
        ["diskutil", "info", vault_disk],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _is_filevault_encrypted(vault_disk: str) -> bool:
    """Inspect ``diskutil apfs list`` to detect FileVault encryption.

    Snippet we parse::

        |   Name: backup-vault (Case-insensitive)
        |   Mount Point: ...
        |   ...
        |   FileVault: Yes

    The volume name and its FileVault flag appear in adjacent lines.
    """
    result = subprocess.run(
        ["diskutil", "apfs", "list"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    lines = result.stdout.splitlines()
    for i, line in enumerate(lines):
        if vault_disk in line:
            for follow in lines[i:i + 6]:
                if "FileVault:" in follow and "Yes" in follow:
                    return True
            return False
    return False


def _get_volume_uuid(vault_disk: str) -> str:
    """Extract the Volume UUID from ``diskutil info``."""
    result = subprocess.run(
        ["diskutil", "info", vault_disk],
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if "Volume UUID" in line:
            return line.rsplit(":", 1)[1].strip()
    raise SyncAborted(f"Could not determine Volume UUID for {vault_disk}")


def _unlock_encrypted_volume(vault_disk: str) -> None:
    """Fetch passphrase from Keychain, pipe it to diskutil. Raises with
    a useful message if the Keychain entry is missing or wrong — the
    user gets the exact ``security add-generic-password`` command to
    fix it."""
    volume_uuid = _get_volume_uuid(vault_disk)

    keychain = subprocess.run(
        ["security", "find-generic-password", "-a", volume_uuid, "-w"],
        capture_output=True,
        text=True,
    )
    if keychain.returncode != 0 or not keychain.stdout.strip():
        error(f"No passphrase found in Keychain for volume UUID {volume_uuid}")
        error(
            "Add it with: security add-generic-password "
            f'-a "{volume_uuid}" -s "{volume_uuid}" '
            f'-D "APFS Volume Password" -l "{vault_disk}" -w \'PASSPHRASE\''
        )
        raise SyncAborted("Backup disk passphrase not in Keychain")

    passphrase = keychain.stdout.rstrip("\n")
    unlock = subprocess.run(
        ["diskutil", "apfs", "unlockVolume", vault_disk, "-stdinpassphrase"],
        input=passphrase,
        text=True,
        capture_output=True,
    )
    if unlock.returncode != 0:
        raise SyncAborted(
            "Failed to unlock backup disk. Wrong passphrase in Keychain?"
        )


def _mount_plain_volume(vault_disk: str) -> None:
    result = subprocess.run(
        ["diskutil", "mount", vault_disk],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SyncAborted("Failed to mount backup disk")


# ── Filesystem capability ──────────────────────────────────────────────────

def probe_filesystem(mount_point: Path) -> None:
    """Refuse if the mounted filesystem doesn't honor BSD ``uchg`` flags.

    Without this guard the engine would happily write to SMB, NFS, or
    exFAT and silently downgrade its append-only contract to "files we
    hope nobody touches." ``stat -f %T`` queries the kernel for the
    live filesystem type of the mount; we trust the answer.
    """
    header("Filesystem capability")

    fs_type = _stat_fs_type(mount_point)

    if fs_type in APPLE_FILESYSTEMS:
        info("Vault filesystem honors BSD uchg — append-only contract enforceable")
        return

    if fs_type in NETWORK_FILESYSTEMS:
        error(
            f"Vault filesystem is {fs_type!r} — network shares do not honor "
            "BSD immutability flags."
        )
        error("  The external-disk engine enforces kernel-level append-only via chflags uchg,")
        error("  which only works on APFS or HFS+ on attached storage.")
        error("  For network/NAS backup, wait for the 'restic' engine (planned).")
        raise SyncAborted(
            f"Vault filesystem {fs_type!r} does not support BSD immutability flags"
        )

    if fs_type in REMOVABLE_FILESYSTEMS:
        error(f"Vault filesystem is {fs_type!r} — does not support BSD immutability flags.")
        error("  Reformat the disk as APFS (Disk Utility → Erase) to use this engine.")
        raise SyncAborted(
            f"Vault filesystem {fs_type!r} does not support BSD immutability flags"
        )

    error(f"Vault filesystem {fs_type!r} is not on the supported list (apfs, hfs).")
    error("  If you believe this filesystem honors chflags uchg, open an issue.")
    raise SyncAborted(f"Vault filesystem {fs_type!r} not supported")


def _stat_fs_type(mount_point: Path) -> str:
    """Return the filesystem type as macOS reports it (e.g. ``"apfs"``,
    ``"smbfs"``, ``"msdos"``). Empty string if the mount point isn't
    currently mounted or the type can't be determined.

    Parses ``mount`` output rather than ``stat`` — BSD ``stat -f %T``
    returns the ls-F file type suffix (``/`` for dirs), not the
    filesystem type. The first attribute in the parenthesized list of
    a mount line is what we want::

        /dev/disk5s1 on /Volumes/foo (apfs, local, nodev, nosuid)
                                      ^^^^
    """
    result = subprocess.run(
        ["mount"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""

    target = str(mount_point)
    for line in result.stdout.splitlines():
        # Each line: "<device> on <mount-point> (<attrs>)"
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


# ── Free space ─────────────────────────────────────────────────────────────

def check_vault_space(mount_point: Path) -> None:
    """Warn if the vault has under 5 GB free. Doesn't abort — running
    low doesn't break the contract; it's just useful information."""
    header("Backup disk space")

    result = subprocess.run(
        ["df", "-m", str(mount_point)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        warn("Could not determine free space")
        return

    try:
        last_line = result.stdout.strip().splitlines()[-1]
        free_mb = int(last_line.split()[3])
    except (IndexError, ValueError):
        warn("Could not parse df output")
        return

    free_gb = free_mb // 1024
    if free_mb < 5120:
        warn(f"Backup disk has only {free_gb}GB free — running low")
    else:
        info(f"{free_gb}GB free")


def _parse_rsync_transferred(stats_output: str) -> int:
    """Extract 'Number of files transferred' from rsync --stats output."""
    for line in stats_output.splitlines():
        if line.startswith("Number of files transferred:"):
            try:
                return int(line.split(":")[-1].strip().replace(",", ""))
            except ValueError:
                return 0
    return 0


# ── Sync data ──────────────────────────────────────────────────────────────

def sync_data(
    sources: List[Source],
    mount_point: Path,
    log_path: Path,
    dry_run: bool,
    verbose: bool,
) -> List[SourceResult]:
    """rsync each source into its vault subdirectory, then lock new files.

    The append-only contract holds because of two cooperating flags:

    * ``--ignore-existing``: rsync skips files already on the vault.
      They keep their ``uchg`` flag from a prior run — never unlocked,
      never overwritten.
    * ``chflags uchg``: applied only to NEW files (files that didn't
      have the flag after rsync). Zero unlock window for existing data.

    rsync exit codes 23 (partial transfer / vanished files) and 24
    (vanished source files) are acceptable for incremental backups —
    they happen when source files move during a sync. Any other
    non-zero exit is a real failure.

    rsync stderr goes to the audit log so the terminal stays tidy but
    the failure trail survives. ``--stats`` output still goes to stdout
    for the user to read.
    """
    header("Syncing data")

    results: List[SourceResult] = []
    for src in sources:
        dest = mount_point / src.vault_subdir
        before_count = count_files(dest) if dest.is_dir() else 0

        if not dry_run:
            dest.mkdir(parents=True, exist_ok=True)

        info(f"{src.display}: {src.src_path}/ → {dest}/")

        rsync_flags = [
            "-a",
            "--ignore-existing",
            "--stats",
            "--exclude=.DS_Store",
            "--exclude=.Spotlight-V100",
            "--exclude=.fseventsd",
            "--exclude=.Trashes",
            "--exclude=._*",
        ]
        if dry_run:
            rsync_flags.append("--dry-run")
        if verbose:
            rsync_flags.append("-v")

        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a") as log_file:
                rsync = subprocess.run(
                    ["/usr/bin/rsync", *rsync_flags,
                     f"{src.src_path}/", f"{dest}/"],
                    capture_output=True,
                    text=True,
                )
                if rsync.stderr:
                    log_file.write(rsync.stderr)
        except FileNotFoundError:
            error(f"{src.display}: /usr/bin/rsync not found")
            results.append(SourceResult(src.id, src.display, "FAILED", 0, 0))
            continue

        if verbose and rsync.stdout:
            print(rsync.stdout, flush=True)

        if rsync.returncode not in RSYNC_OK_CODES:
            error(f"{src.display}: rsync failed (exit {rsync.returncode})")
            results.append(SourceResult(src.id, src.display, "FAILED", 0, 0))
            continue

        if dry_run:
            new_count = _parse_rsync_transferred(rsync.stdout)
            after_count = before_count + new_count
        else:
            after_count = count_files(dest)
            new_count = after_count - before_count

        # Lock only the new files. Existing locked files weren't touched
        # (--ignore-existing), so their uchg flag survives.
        # BSD find: `! -flags +uchg` matches files lacking the flag.
        #
        # The lock IS the append-only guarantee. If chflags fails the new
        # files are sitting unlocked on the vault, so a silent success
        # here would be a lie — mark the source FAILED rather than report
        # protection we didn't apply.
        if new_count > 0 and not dry_run:
            lock = subprocess.run(
                ["find", str(dest), "-type", "f",
                 "!", "-flags", "+uchg",
                 "-exec", "chflags", "uchg", "{}", "+"],
                capture_output=True,
                text=True,
            )
            if lock.returncode != 0:
                error(
                    f"{src.display}: failed to lock {format_number(new_count)} "
                    f"new files (chflags exit {lock.returncode}) — append-only "
                    "protection not applied"
                )
                if lock.stderr.strip():
                    append_log(log_path, f"{src.display} chflags: {lock.stderr.strip()}")
                results.append(SourceResult(src.id, src.display, "FAILED",
                                            after_count, new_count))
                continue
            info(f"{src.display}: locked {format_number(new_count)} new files")

        results.append(SourceResult(
            id=src.id,
            display=src.display,
            status="ok",
            total_files=after_count,
            new_files=new_count,
        ))
        if dry_run:
            info(
                f"{src.display}: {format_number(new_count)} files "
                f"would be written to backup archive"
            )
        else:
            info(
                f"{src.display}: done "
                f"({format_number(after_count)} total, "
                f"{format_number(new_count)} new)"
            )

    return results


# ── Verify (optional) ──────────────────────────────────────────────────────

def verify_sync(sources: List[Source], mount_point: Path) -> None:
    """Compare file counts source vs. vault. Logs only — never aborts.

    Useful sanity check after a first run or a manual restore. Off by
    default because counting tens of thousands of files takes a moment.
    """
    header("Verifying sync")

    for src in sources:
        dest = mount_point / src.vault_subdir
        src_count = count_files(src.src_path)
        dest_count = count_files(dest)
        if dest_count >= src_count:
            info(
                f"{src.display}: source={format_number(src_count)}, "
                f"vault={format_number(dest_count)} — ok"
            )
        else:
            warn(
                f"{src.display}: source={format_number(src_count)}, "
                f"vault={format_number(dest_count)} — vault has fewer files"
            )


# ── Eject ──────────────────────────────────────────────────────────────────

def eject_vault(vault_disk: str, dry_run: bool, no_eject: bool) -> None:
    """Best-effort eject of the vault's parent disk.

    Works from a Terminal session. Sandbox-blocked from cron — that
    failure is logged but does not fail the sync. Files are
    ``uchg``-protected regardless of whether the disk is mounted.
    """
    if dry_run:
        info("[DRY RUN] Would eject backup disk")
        return
    if no_eject:
        info("Backup disk left mounted (--no-eject)")
        return

    header("Ejecting backup disk")

    parent_disk = _get_parent_disk(vault_disk)
    if not parent_disk:
        warn("Could not find parent disk — try ejecting manually")
        return

    info(f"Ejecting {parent_disk}...")
    result = subprocess.run(
        ["diskutil", "eject", parent_disk],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        info("Backup disk ejected")
    else:
        warn(
            "Eject failed (sandbox-blocked from this context) — "
            "disk stays mounted, files are protected by uchg"
        )


def _get_parent_disk(vault_disk: str) -> str:
    """Resolve volume → parent disk (``disk5s1`` → ``disk5``) via the
    ``Part of Whole`` field. More robust than parsing the id."""
    result = subprocess.run(
        ["diskutil", "info", vault_disk],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        if "Part of Whole" in line:
            return line.split(":", 1)[1].strip()
    return ""


# ── History (append-only run log) ──────────────────────────────────────────

def append_to_history(result: SyncResult, history_path: Path) -> None:
    """Append one JSON object as a single line to ``history.jsonl``.

    JSONL means each run is a self-contained line — partial writes from
    a crashed engine produce at most one unparseable trailing line,
    which :func:`read_latest_run` skips. POSIX guarantees writes under
    PIPE_BUF (4KB) are atomic against concurrent appenders; a run
    record is ~500 bytes so we're well under.

    Best-effort: if the write fails (full disk, permissions), log to
    stderr and continue. The sync itself may have succeeded; failing
    to record the outcome shouldn't mask that.
    """
    try:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        # Single write() — open() in append mode, dump, newline, close.
        # No partial-line risk because the write fits in one syscall.
        line = json.dumps(_result_to_dict(result)) + "\n"
        with history_path.open("a") as f:
            f.write(line)
    except OSError as e:
        print(
            f"warning: could not append to {history_path}: {e}",
            file=sys.stderr,
        )


def read_latest_run(history_path: Path) -> Optional[dict]:
    """Return the most recent run from ``history.jsonl``, or ``None``.

    Scans the whole file and returns the last parseable line. This
    tolerates a corrupted trailing line (e.g. a crash mid-write that
    somehow truncated below the POSIX-atomic threshold): we walk past
    bad lines and keep the last good one.

    At realistic sizes (~500 bytes per run, ~200 KB per year of
    nightly runs) reading the whole file is instant. Reverse-seek is
    overengineering until the file outgrows that, which it won't soon.
    """
    if not history_path.exists():
        return None
    latest: Optional[dict] = None
    try:
        with history_path.open() as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    latest = json.loads(stripped)
                except json.JSONDecodeError:
                    # Corrupted line — skip and keep scanning. The last
                    # good line wins.
                    continue
    except OSError:
        return None
    return latest


def _result_to_dict(result: SyncResult) -> dict:
    """Convert to a plain dict; coerce empty ``failure_reason`` to JSON
    null so the caller doesn't have to distinguish ``""`` from "no
    failure"."""
    d = asdict(result)
    if not d.get("failure_reason"):
        d["failure_reason"] = None
    return d


# ── Context probes ─────────────────────────────────────────────────────────

def detect_run_context() -> str:
    """Best-guess at how the script was invoked. Affects status
    reporting — was this a manual run from Terminal or the nightly
    cron job? launchd sets ``XPC_SERVICE_NAME``; Terminal sets
    ``TERM_PROGRAM``; cron sets neither."""
    if os.environ.get("TERM_PROGRAM"):
        return "Terminal"
    if os.environ.get("XPC_SERVICE_NAME"):
        return "launchd"
    return "cron"


def detect_vault_state(mount_point: Path, drive_not_connected: bool) -> str:
    """Classify the vault's final state for the result JSON.

    ``not_connected`` is reported only when diskutil couldn't see the
    volume at all — distinct from ``ejected``, which means we
    successfully unmounted after a sync.
    """
    if drive_not_connected:
        return "not_connected"
    if mount_point.is_dir():
        return "mounted"
    return "ejected"


def measure_vault_size(mount_point: Path) -> str:
    """``du -sh`` on the vault's ``data/`` subtree if present, else the
    whole mount. Returns ``"unknown"`` on failure rather than raising —
    size is a nice-to-have field, not a contract."""
    data_dir = mount_point / "data"
    target = data_dir if data_dir.is_dir() else mount_point
    if not target.is_dir():
        return "unknown"
    result = subprocess.run(
        ["du", "-sh", str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout:
        return "unknown"
    return result.stdout.split()[0]


# ── Pipeline ───────────────────────────────────────────────────────────────

def run_sync(
    backup_data_dir: Path,
    vault_disk: str,
    sources_env: str,
    args: argparse.Namespace,
) -> int:
    """Top-level pipeline. Returns the process exit code (0 ok, 1 fail).

    Always appends to ``history.jsonl``, even when an exception
    interrupts the pipeline halfway through. The caller (orchestrator)
    treats an empty/missing history file as "the engine crashed before
    it could report" — distinct from "the engine reported a failure."

    The canary at ``BACKUP_DATA_DIR/canary`` is expected to exist (planted
    by ``on_install``). Missing canary = abort.
    """
    mount_point = Path("/Volumes") / vault_disk
    log_path = backup_data_dir / "logs" / "sync.log"
    history_path = backup_data_dir / "logs" / "history.jsonl"
    canary_file = backup_data_dir / "canary"

    result = SyncResult(
        dry_run=args.dry_run,
        run_context=detect_run_context(),
        run_user=os.environ.get("USER", ""),
        vault_disk=vault_disk,
        started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    started_at_seconds = time.time()
    drive_not_connected_flag = False

    append_log(
        log_path,
        f"Starting sync (dry_run={args.dry_run}, no_eject={args.no_eject})",
    )

    try:
        sources = parse_sources(sources_env)

        print()
        print(f"{BOLD}═══ external-disk sync — vault: {vault_disk} ═══{NC}")
        if args.dry_run:
            print(f"  {YELLOW}DRY RUN — no changes will be made{NC}")

        verify_canary(canary_file)
        preflight_check_sources(sources)
        mount_vault(vault_disk, mount_point, args.dry_run)
        if not args.dry_run:
            probe_filesystem(mount_point)
        check_vault_space(mount_point)

        result.sources = sync_data(
            sources, mount_point, log_path, args.dry_run, args.verbose
        )
        if any(r.status == "FAILED" for r in result.sources):
            result.success = False

        # Measure vault size BEFORE eject — once the disk is ejected the
        # mount point is gone and du has nothing to look at.
        result.vault_size = measure_vault_size(mount_point)

        if args.verify:
            verify_sync(sources, mount_point)

        eject_vault(vault_disk, args.dry_run, args.no_eject)

    except DriveNotConnected as e:
        drive_not_connected_flag = True
        result.success = False
        result.failure_reason = str(e)
        append_log(log_path, f"ABORTED: {e}")
    except SyncAborted as e:
        result.success = False
        result.failure_reason = str(e)
        append_log(log_path, f"ABORTED: {e}")
    except Exception as e:
        result.success = False
        result.failure_reason = f"Unexpected error: {e}"
        append_log(log_path, f"ERROR: {e}")
    finally:
        result.ended_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        result.duration_seconds = int(time.time() - started_at_seconds)
        result.vault_state = detect_vault_state(mount_point, drive_not_connected_flag)
        if result.vault_size == "unknown":
            result.vault_size = measure_vault_size(mount_point)
        append_to_history(result, history_path)
        append_log(log_path, f"Sync finished (success={result.success})")

    print()
    if result.success:
        print(f"{GREEN}Sync completed successfully.{NC}")
        if args.dry_run:
            print(f"  {BOLD}Dry run, nothing synced!{NC}")
        return 0
    print(f"{RED}Sync completed with errors.{NC}")
    if args.dry_run:
        print(f"  {BOLD}Dry run, nothing synced!{NC}")
    return 1


# ── Entry point ────────────────────────────────────────────────────────────

def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="external-disk engine — append-only sync of stacklet data.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be synced (no changes).",
    )
    parser.add_argument(
        "--no-eject", action="store_true",
        help="Keep vault mounted after sync.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show rsync file-level details.",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Compare file counts source vs vault after sync.",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)

    try:
        backup_data_dir = Path(os.environ["BACKUP_DATA_DIR"])
        vault_disk = os.environ["VAULT_DISK"]
    except KeyError as e:
        missing = e.args[0]
        print(f"sync: required environment variable {missing} is not set",
              file=sys.stderr)
        return 1

    sources_env = os.environ.get("SOURCES", "")

    if str(backup_data_dir).startswith("/Volumes/"):
        print(
            f"sync: BACKUP_DATA_DIR ({backup_data_dir}) must not be under "
            "/Volumes/. Logs and result state belong on the internal SSD.",
            file=sys.stderr,
        )
        return 1

    return run_sync(backup_data_dir, vault_disk, sources_env, args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
