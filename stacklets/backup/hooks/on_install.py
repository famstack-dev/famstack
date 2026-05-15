"""on_install — set up host-side state and install the nightly cron entry.

Runs once after on_configure on first ``stack up backup``. Idempotent.

Steps:

* Create ``BACKUP_DATA_DIR`` and its ``logs/`` subdir.
* Plant the canary tripwire.
* Install the cron entry that fires the nightly sync.
* Walk the user through granting Full Disk Access to ``/usr/sbin/cron``
  so the scheduled run can reach the vault disk.

The cron command invokes ``./stack backup sync`` directly with output
redirected to ``BACKUP_DATA_DIR/logs/cron.log``. No ``.app`` wrapper:
granting FDA to ``/usr/sbin/cron`` covers every cron job on the
system, which is the trade-off the user accepts in exchange for not
maintaining a custom app bundle.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


_BACKUP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKUP_DIR))

from _config import read_target  # noqa: E402
import _cron as cron  # noqa: E402

# Single source of truth for the canary contents — the engine verifies
# what install plants.
_ENGINE_DIR = _BACKUP_DIR / "engines" / "external-disk"
sys.path.insert(0, str(_ENGINE_DIR))
from sync import CANARY_STRING  # noqa: E402

from stack.prompt import (  # noqa: E402
    bold, confirm, dim, done, nl, out, section, warn,
)


TARGET_NAME = "vault"


# ── Hook entry ─────────────────────────────────────────────────────────────

def run(ctx):
    instance_dir = Path(ctx.stack.instance_dir)
    repo_root = Path(ctx.stack.root)
    backup_data_dir = Path(ctx.env["BACKUP_DATA_DIR"])

    target = read_target(instance_dir / "stack.toml", TARGET_NAME)
    if target is None:
        raise RuntimeError(
            f"Target '{TARGET_NAME}' is not in stack.toml. "
            "Did on_configure run successfully?"
        )

    section("Backup install", f"Host state + cron entry for target '{TARGET_NAME}'")
    nl()

    # 1. Directories the engine needs.
    backup_data_dir.mkdir(parents=True, exist_ok=True)
    (backup_data_dir / "logs").mkdir(parents=True, exist_ok=True)
    done(f"State directory: {backup_data_dir}")

    # 2. Canary planting.
    plant_canary(backup_data_dir)

    # 3. Cron entry.
    schedule = target.get("schedule", "0 2 * * *")
    cron_command = _cron_command(repo_root, backup_data_dir)
    try:
        changed = cron.install_entry(schedule, cron_command, TARGET_NAME)
    except RuntimeError as e:
        raise RuntimeError(
            f"Cron install failed: {e}\n"
            f"Add this line to your crontab manually (crontab -e):\n"
            f"  {schedule} {cron_command}  # {cron.marker_for(TARGET_NAME)}"
        )
    if changed:
        done(f"Cron entry installed: {schedule}")
    else:
        done("Cron entry already up to date")

    # 4. FDA walkthrough for cron.
    if sys.stdin.isatty():
        _fda_walkthrough()
    else:
        warn("Non-interactive install — grant Full Disk Access to /usr/sbin/cron")
        out("  System Settings → Privacy & Security → Full Disk Access → + → /usr/sbin/cron")
        nl()

    bold("Setup complete.")
    out("Run a sync now:       stack backup sync")
    out("Check the last run:   stack backup status")
    nl()


# ── Cron command ───────────────────────────────────────────────────────────

def _cron_command(repo_root: Path, backup_data_dir: Path) -> str:
    """The shell command cron runs.

    Output is appended to ``cron.log`` under the state dir so a
    misbehaving scheduled run leaves a trail.
    """
    log_path = backup_data_dir / "logs" / "cron.log"
    stack_bin = repo_root / "stack"
    return f"{stack_bin} backup sync >> {log_path} 2>&1"


# ── Canary planter ────────────────────────────────────────────────────────

def plant_canary(backup_data_dir: Path) -> None:
    """Write the ransomware-tripwire canary with known content.

    The engine verifies this file before every sync and refuses to
    proceed if it's missing or corrupted (see ``verify_canary`` in the
    engine). Install plants it; the engine never creates it.

    Idempotent: if the canary already exists we leave it alone.
    Clobbering an existing canary would make a tampered-with state
    indistinguishable from a fresh install.
    """
    canary_path = backup_data_dir / "canary"
    if canary_path.exists():
        done(f"Canary already present: {canary_path}")
        return
    canary_path.write_text(CANARY_STRING + "\n")
    done(f"Canary planted: {canary_path}")


# ── FDA walkthrough ────────────────────────────────────────────────────────

def _fda_walkthrough() -> None:
    """Walk the user through granting Full Disk Access to ``/usr/sbin/cron``.

    Without this grant, cron-invoked processes can't read or write
    files under ``/Volumes/*`` and the nightly sync silently fails.
    macOS won't let us script the grant — TCC requires user
    interaction — so we deep-link to the right Settings pane and
    instruct.
    """
    bold("Full Disk Access for cron")
    out("Cron-invoked processes can't reach the vault disk without an")
    out("explicit Full Disk Access grant. macOS won't let us automate")
    out("this; you grant it once and every cron job inherits the access.")
    nl()
    out("Steps:")
    out("  1. Settings opens to the Full Disk Access pane.")
    out("  2. Click + to add an app.")
    out("  3. Press Cmd+Shift+G and paste:  /usr/sbin/cron")
    out("  4. Select cron and turn it on. Authenticate when asked.")
    nl()
    dim("Scope note: this grants FDA to every cron job on this Mac, not")
    dim("just famstack's. If you have other cron jobs and prefer to scope")
    dim("the grant, the alternative is a dedicated .app wrapper (planned).")
    nl()

    subprocess.run([
        "open",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
    ], check=False)

    if not confirm("Done? (you don't have to relaunch anything)", default=True):
        warn("Skipping FDA confirmation — scheduled syncs may fail until granted.")
        dim("  Run 'stack up backup' again later to re-trigger this prompt.")
    nl()
