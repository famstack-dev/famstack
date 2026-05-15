"""on_start — ensure the cron entry is present.

Runs on every ``stack up backup``. Idempotent — install_entry replaces
an existing entry if the schedule or command changed (e.g. data_dir
was reconfigured, schedule was edited in stack.toml), and is a no-op
when the entry is already current.

This hook is the natural place to pick up stack.toml edits: a user
changes ``schedule`` from 02:00 to 03:30, runs ``stack up backup``, the
cron entry updates. No need to ``stack destroy`` + reconfigure.
"""

from __future__ import annotations

import sys
from pathlib import Path


_BACKUP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKUP_DIR))

from _config import read_target  # noqa: E402
import _cron as cron  # noqa: E402


TARGET_NAME = "vault"
APP_BUNDLE_NAME = "FamstackVaultSync.app"


def run(ctx):
    instance_dir = Path(ctx.stack.instance_dir)
    backup_data_dir = Path(ctx.env["BACKUP_DATA_DIR"])

    target = read_target(instance_dir / "stack.toml", TARGET_NAME)
    if target is None:
        # No target configured. on_configure should have caught this,
        # but be defensive — silently skipping here would leave the
        # user with no scheduled run and no warning.
        ctx.step(f"No [backup.targets.{TARGET_NAME}] in stack.toml — skipping cron install")
        return

    app_path = backup_data_dir / APP_BUNDLE_NAME
    if not app_path.is_dir():
        # The .app should have been installed by on_install. If it's
        # missing here, something deleted it after install — re-running
        # `stack up backup` should regenerate it via on_install, but
        # the framework only runs on_install once. Surface the issue.
        ctx.step(
            f"App bundle missing at {app_path}. "
            f"Run 'stack destroy backup && stack up backup' to reinstall."
        )
        return

    schedule = target.get("schedule", "0 2 * * *")
    try:
        changed = cron.install_entry(schedule, f"open {app_path}", TARGET_NAME)
    except RuntimeError as e:
        raise RuntimeError(
            f"Cron install failed: {e}\n"
            f"Add this line to your crontab manually (crontab -e):\n"
            f"  {schedule} open {app_path}  # {cron.marker_for(TARGET_NAME)}"
        )
    if changed:
        ctx.step(f"Cron entry updated for target '{TARGET_NAME}' ({schedule})")
    else:
        ctx.step(f"Cron entry already current for target '{TARGET_NAME}'")
