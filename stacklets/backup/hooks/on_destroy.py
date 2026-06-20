"""on_destroy — tear down host-side state. NEVER touches existing
backup data.

The framework calls on_stop FIRST during destroy, then on_destroy.
The cron entry is removed in on_stop; this hook removes it AGAIN as
a defensive measure. Both removals are idempotent (no-op if the entry
is already gone) so the double-call has no cost and protects against
on_stop having been skipped, failed, or never run.

What this hook removes (regenerable host-side state):
  - The cron entry installed by on_install (defensive re-removal).

What is explicitly preserved:
  - The vault disk and every file on it. The whole point of an
    append-only archive is that it outlives the system that wrote it.
    A user who runs ``stack destroy backup`` is uninstalling the
    backup tooling, not asking us to wipe their photo history.
  - The macOS Keychain entry for the disk passphrase (encrypted vaults
    only). The user may want manual disk access after uninstall.
  - The Full Disk Access grant on ``/usr/sbin/cron``. It also covers
    any other cron jobs on the system; we can't remove TCC grants
    programmatically anyway, and the user may want to keep it.

The vault is on ``/Volumes/<disk>``, not under BACKUP_DATA_DIR — the
framework's data-dir cleanup never reaches it. on_configure refuses
to let BACKUP_DATA_DIR be placed under ``/Volumes/`` as a defensive
measure against misconfiguration.
"""

from __future__ import annotations

import sys
from pathlib import Path


_BACKUP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKUP_DIR))

import _cron as cron  # noqa: E402

from stack.prompt import dim, done, nl, out  # noqa: E402


def run(ctx):
    # Defensive cron sweep — should already be empty after on_stop, but
    # cheap to verify. Wildcard rather than per-target because by destroy
    # time we may have no target config left to iterate.
    try:
        removed = cron.remove_all_entries()
        if removed:
            done(f"Removed {removed} stale backup cron entr{'y' if removed == 1 else 'ies'}")
    except RuntimeError as e:
        # Loud failure: a stale cron entry firing nightly against an
        # uninstalled backup is the worst-case operational outcome.
        raise RuntimeError(
            f"Could not remove backup cron entries: {e}\n"
            f"Run 'crontab -e' and delete any line containing 'famstack-backup-'."
        )

    nl()
    out("Preserved:")
    out("  • Vault disk contents — append-only archive outlives the tooling.")
    out("    To wipe (only if you're sure): plug in the disk, then")
    dim("       sudo chflags -R nouchg /Volumes/<disk>/data && rm -rf /Volumes/<disk>/data")
    out("  • Keychain passphrase entry (encrypted vaults). Remove with:")
    dim("       security delete-generic-password -a '<volume-uuid>'")
    out("  • Full Disk Access grant on /usr/sbin/cron. Leave it if you")
    out("    have other cron jobs; otherwise remove via Privacy & Security.")
    nl()
