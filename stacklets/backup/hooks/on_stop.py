"""on_stop — remove the nightly cron entry.

MUST remove every cron entry installed by on_install. We iterate over
all ``[backup.targets.*]`` names in stack.toml — once we support
multiple targets, each gets its own cron entry and its own removal.
A wildcard fallback (``remove_all_entries``) catches the case where
stack.toml is missing or unreadable.

Idempotent: re-running after a successful stop is a no-op.
BACKUP_DATA_DIR, the vault disk, and the Keychain passphrase are left
alone — ``stack up backup`` reinstalls the cron without re-running
on_configure or on_install.

If the crontab edit fails (locked file, permission denied), this hook
raises with the exact line to remove manually. Silent failure here is
dangerous: a stale entry would keep firing nightly against a stopped
backup, appending failure records to ``history.jsonl`` indefinitely.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List


_BACKUP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKUP_DIR))

import _cron as cron  # noqa: E402

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    from stack._vendor import tomli as tomllib  # type: ignore


def run(ctx):
    instance_dir = Path(ctx.stack.instance_dir)
    stack_toml = instance_dir / "stack.toml"

    target_names = _read_target_names(stack_toml)
    if target_names:
        removed_any = False
        for name in target_names:
            if cron.remove_entry(name):
                ctx.step(f"Removed cron entry for target '{name}'")
                removed_any = True
        if not removed_any:
            ctx.step("No backup cron entries to remove")
        return

    # Fallback: stack.toml unreadable or no targets listed. Sweep
    # anything tagged with our marker prefix.
    removed = cron.remove_all_entries()
    if removed:
        ctx.step(f"Removed {removed} backup cron entr{'y' if removed == 1 else 'ies'}")
    else:
        ctx.step("No backup cron entries to remove")


def _read_target_names(stack_toml: Path) -> List[str]:
    if not stack_toml.exists():
        return []
    try:
        with stack_toml.open("rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        return []
    return list(data.get("backup", {}).get("targets", {}).keys())
