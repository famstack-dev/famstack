"""Crontab install/remove helpers for the backup stacklet.

Backup's nightly run is a cron entry rather than a launchd job:
launchd's sandbox blocks ``diskutil`` and ``/Volumes/`` access even
with Full Disk Access, while cron inherits the FDA grant on
``/usr/sbin/cron`` once the user adds it in System Settings.

Entries are identified by an inline marker comment::

    0 2 * * * /path/to/stack backup sync ...  # famstack-backup-vault

The marker has the target name appended so multiple targets (vault,
offsite) can coexist without one's removal touching the other's entry.
``install_entry`` and ``remove_entry`` are both idempotent — running
either twice is harmless.

The contract: ``stack down backup`` and ``stack destroy backup`` MUST
both leave the user's crontab free of our entries. If an edit fails
(locked file, permission denied) we raise with the exact line the user
should remove manually rather than failing silently — a stale cron
entry firing nightly against an uninstalled backup is the worst
operational outcome (see ``project_backup_lifecycle.md``).
"""

from __future__ import annotations

import subprocess
from typing import List


MARKER_PREFIX = "# famstack-backup-"


def marker_for(target_name: str) -> str:
    """Return the inline marker string for a given target. The string
    is what gets appended to the cron line after ``#`` so removal can
    find and drop it again."""
    return f"famstack-backup-{target_name}"


def install_entry(schedule: str, command: str, target_name: str) -> bool:
    """Install a cron entry for ``target_name`` with the given schedule
    and command.

    Idempotent: if an entry with this target's marker is already
    present, it's replaced rather than duplicated. Returns ``True`` if
    the crontab changed, ``False`` if the desired entry was already
    present byte-for-byte.

    Raises ``RuntimeError`` if the ``crontab`` command fails — the
    caller surfaces the exact line for manual installation.
    """
    marker = marker_for(target_name)
    desired_line = f"{schedule} {command}  # {marker}"

    current = _read_crontab()
    without_ours = [line for line in current if marker not in line]
    new_lines = without_ours + [desired_line]

    if new_lines == current:
        return False

    _write_crontab("\n".join(new_lines) + "\n")
    return True


def remove_entry(target_name: str) -> bool:
    """Remove the cron entry for ``target_name`` if present.

    Idempotent — returns ``False`` when no matching entry was found
    (already removed, or never installed). ``True`` when an entry was
    actually removed.
    """
    marker = marker_for(target_name)
    current = _read_crontab()
    filtered = [line for line in current if marker not in line]
    if filtered == current:
        return False
    _write_crontab("\n".join(filtered) + "\n" if filtered else "")
    return True


def is_installed(target_name: str) -> bool:
    """True if a cron entry tagged with this target's marker is present
    in the current user's crontab."""
    marker = marker_for(target_name)
    return any(marker in line for line in _read_crontab())


def remove_all_entries() -> int:
    """Remove every cron entry whose marker matches ``famstack-backup-*``.

    Useful in destroy paths when we may have lost track of which target
    names exist (stack.toml deleted, partial install). Returns the
    number of entries removed.
    """
    current = _read_crontab()
    filtered = [line for line in current if MARKER_PREFIX not in line]
    removed = len(current) - len(filtered)
    if removed == 0:
        return 0
    _write_crontab("\n".join(filtered) + "\n" if filtered else "")
    return removed


# ── crontab I/O ────────────────────────────────────────────────────────────

def _read_crontab() -> List[str]:
    """Current user's crontab as a list of lines.

    Returns ``[]`` for "no crontab installed" — the typical first-run
    state on a fresh Mac. Other crontab failures (locked, permission
    denied) also return ``[]`` so the caller's subsequent write
    attempt is the one that surfaces the real error.
    """
    result = subprocess.run(
        ["crontab", "-l"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()


def _write_crontab(content: str) -> None:
    """Replace the current user's crontab with ``content``.

    Raises :class:`RuntimeError` with the crontab command's stderr on
    failure. We never want to swallow this — a write failure means our
    install or remove didn't actually happen.
    """
    result = subprocess.run(
        ["crontab", "-"],
        input=content, text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"crontab edit failed (exit {result.returncode}): "
            f"{result.stderr.strip() or 'no error output'}"
        )
