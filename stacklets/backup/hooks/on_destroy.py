"""on_destroy — tear down host-side state. NEVER touches existing
backup data.

The framework calls on_stop FIRST during destroy, then on_destroy. The
cron entry is removed in on_stop; this hook removes it AGAIN as a
defensive measure. Both removals are idempotent (no-op if the entry is
already gone) so the double-call has no cost and protects against
on_stop having been skipped, failed, or never run.

What this hook removes (regenerable host-side state):
  - The cron entry installed by on_install (defensive re-removal)
  - The FamstackVaultSync.app bundle — the framework's data-dir
    cleanup will sweep it after this hook, but removing it explicitly
    here makes the destroy-time summary accurate

What is explicitly preserved:
  - The vault disk and every file on it. The whole point of an
    append-only archive is that it outlives the system that wrote it.
    A user who runs ``stack destroy backup`` is uninstalling the
    backup tooling, not asking us to wipe their photo history.
  - The macOS Keychain entry for the disk passphrase (encrypted vaults
    only). The user may want manual disk access after uninstall.

The vault is on ``/Volumes/<disk>``, not under BACKUP_DATA_DIR — the
framework's data-dir cleanup never reaches it. on_configure refuses to
let BACKUP_DATA_DIR be placed under ``/Volumes/`` as a defensive
measure against misconfiguration.

The Full Disk Access entry in System Settings becomes orphaned (the
.app it points at is gone). macOS shows it as "This item refers to an
item that doesn't exist." Users can clean it up manually; we can't
remove TCC entries programmatically — that's the entire point of TCC.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


_BACKUP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKUP_DIR))

import _cron as cron  # noqa: E402

from stack.prompt import dim, done, nl, out  # noqa: E402


APP_BUNDLE_NAME = "FamstackVaultSync.app"


def run(ctx):
    backup_data_dir = Path(ctx.env["BACKUP_DATA_DIR"])

    # Defensive cron sweep — should already be empty after on_stop, but
    # cheap to verify. Use the wildcard sweep rather than a per-target
    # loop because by destroy time we may have no target config left to
    # iterate.
    try:
        removed = cron.remove_all_entries()
        if removed:
            done(f"Removed {removed} stale backup cron entr{'y' if removed == 1 else 'ies'}")
    except RuntimeError as e:
        # Loud failure: a stale cron entry pointed at a now-deleted
        # .app is the worst-case operational outcome — silently failing
        # to clean up is exactly what NOT to do.
        raise RuntimeError(
            f"Could not remove backup cron entries: {e}\n"
            f"Run 'crontab -e' and delete any line containing 'famstack-backup-'."
        )

    # Remove the .app bundle explicitly. The framework's destroy will
    # also wipe BACKUP_DATA_DIR/ recursively after we return, so this
    # is partly cosmetic — but doing it here means the summary printed
    # below reflects reality, not promises.
    app_path = backup_data_dir / APP_BUNDLE_NAME
    if app_path.is_dir():
        shutil.rmtree(app_path, ignore_errors=True)
        done(f"Removed app bundle: {app_path}")

    nl()
    out("Preserved:")
    out("  • Vault disk contents — append-only archive outlives the tooling.")
    out("    To wipe (only if you're sure): plug in the disk, then")
    dim("       sudo chflags -R nouchg /Volumes/<disk>/data && rm -rf /Volumes/<disk>/data")
    out("  • Keychain passphrase entry (encrypted vaults). Remove with:")
    dim("       security delete-generic-password -a '<volume-uuid>'")
    out("  • Full Disk Access entry in System Settings (orphaned — remove")
    out("    manually via Privacy & Security → Full Disk Access → -)")
    nl()
