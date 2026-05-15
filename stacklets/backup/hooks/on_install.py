"""on_install — install the FDA-granted .app, walk the user through the
FDA grant, and add the nightly cron entry.

Runs once after on_configure on first ``stack up backup``. Idempotent —
re-running it after a successful install is a no-op (the .app and cron
entry already exist and get rewritten with the same content).

Why the .app dance: macOS TCC restricts ``diskutil`` operations from
background processes (cron, launchd) unless the binary has been granted
Full Disk Access. FDA can't be granted to a raw script or symlink —
only to a proper .app bundle. So we generate a minimal .app whose only
job is to receive the FDA grant and shell out to ``stack backup sync``.

The cron line invokes the app via ``open``, which routes through the
proper macOS app lifecycle so the FDA permission applies.
"""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path


_BACKUP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKUP_DIR))

from _config import read_target  # noqa: E402
import _cron as cron  # noqa: E402

# Import the canary constant from the engine so the planter writes
# exactly what the verifier expects. Single source of truth.
_ENGINE_DIR = _BACKUP_DIR / "engines" / "external-disk"
sys.path.insert(0, str(_ENGINE_DIR))
from sync import CANARY_STRING  # noqa: E402

from stack.prompt import (  # noqa: E402
    ask, bold, confirm, dim, done, nl, out, section, warn,
)


APP_BUNDLE_NAME = "FamstackVaultSync.app"
APP_BUNDLE_ID = "dev.famstack.backup"
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

    section("Backup install", f"FDA wrapper + cron entry for target '{TARGET_NAME}'")
    nl()

    # 1. Make the directories the engine and the .app both need.
    backup_data_dir.mkdir(parents=True, exist_ok=True)
    (backup_data_dir / "logs").mkdir(parents=True, exist_ok=True)

    # 2. Plant the canary. The engine only *verifies* — it doesn't
    #    create the tripwire — so install is where it gets seeded.
    #    Idempotent: only writes if the file isn't already present
    #    (re-running install must not clobber an existing canary that
    #    might already have been verified across successful syncs).
    plant_canary(backup_data_dir)

    # 3. Generate the .app bundle.
    app_path = generate_app_bundle(
        target_dir=backup_data_dir,
        stack_executable=repo_root / "stack",
        log_path=backup_data_dir / "logs" / "cron.log",
    )
    done(f"App bundle: {app_path}")
    nl()

    # 4. Walk the user through the FDA grant.
    if sys.stdin.isatty():
        _fda_walkthrough(app_path)
    else:
        warn("Non-interactive install — Full Disk Access must be granted manually:")
        out(f"  System Settings → Privacy & Security → Full Disk Access → + → {app_path}")
        nl()

    # 5. Install the cron entry.
    schedule = target.get("schedule", "0 2 * * *")
    cron_command = f"open {app_path}"
    try:
        changed = cron.install_entry(schedule, cron_command, TARGET_NAME)
    except RuntimeError as e:
        raise RuntimeError(
            f"Cron install failed: {e}\n"
            f"Add this line to your crontab manually (crontab -e):\n"
            f"  {schedule} {cron_command}  # {cron.marker_for(TARGET_NAME)}"
        )

    if changed:
        done(f"Cron entry installed: {schedule} → {app_path}")
    else:
        done("Cron entry already up to date")
    nl()

    bold("Setup complete.")
    out("Run 'stack backup sync' to test now (manual run also tries to eject).")
    out("The scheduled run fires nightly per the cron entry. Disk stays")
    out("mounted between scheduled runs (sandbox blocks eject from cron);")
    out("files are protected by chflags uchg.")
    nl()


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


# ── .app bundle generation ────────────────────────────────────────────────

def generate_app_bundle(
    target_dir: Path,
    stack_executable: Path,
    log_path: Path,
) -> Path:
    """Generate the FamstackVaultSync.app bundle.

    A .app bundle is a directory tree macOS treats as a single
    "application." Ours is the bare minimum: an ``Info.plist`` that
    identifies the bundle and an executable that shells out to
    ``stack backup sync``. The bundle exists ONLY so macOS TCC can
    attach a Full Disk Access grant to it — there's no UI, no dock
    icon, no real "app."
    """
    app_path = target_dir / APP_BUNDLE_NAME
    contents_dir = app_path / "Contents"
    macos_dir = contents_dir / "MacOS"
    macos_dir.mkdir(parents=True, exist_ok=True)

    # Info.plist — minimum keys macOS needs to recognize the bundle.
    # LSUIElement=true keeps it out of the dock and Cmd-Tab.
    (contents_dir / "Info.plist").write_text(_info_plist())

    # The executable wrapper. Cron fires `open <app>`, macOS launches
    # the bundle, the bundle's executable runs this script.
    wrapper = macos_dir / "vault-sync"
    wrapper.write_text(_wrapper_script(stack_executable, log_path))
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return app_path


def _info_plist() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        '    <key>CFBundleExecutable</key>\n'
        '    <string>vault-sync</string>\n'
        f'    <key>CFBundleIdentifier</key>\n'
        f'    <string>{APP_BUNDLE_ID}</string>\n'
        '    <key>CFBundleName</key>\n'
        '    <string>FamstackVaultSync</string>\n'
        '    <key>CFBundleVersion</key>\n'
        '    <string>1.0</string>\n'
        '    <key>LSUIElement</key>\n'
        '    <true/>\n'
        '</dict>\n'
        '</plist>\n'
    )


def _wrapper_script(stack_executable: Path, log_path: Path) -> str:
    """The .app's executable. Logs go to BACKUP_DATA_DIR/logs/cron.log
    so a scheduled run that's gone wrong leaves a trail the user can
    inspect without trawling Console.app."""
    return (
        "#!/bin/bash\n"
        "# Auto-generated by stacklets/backup/hooks/on_install.py — do not edit.\n"
        "# Invoked by the cron entry: `open <this-bundle>`.\n"
        f'LOG="{log_path}"\n'
        'mkdir -p "$(dirname "$LOG")"\n'
        f'exec "{stack_executable}" backup sync >> "$LOG" 2>&1\n'
    )


# ── FDA walkthrough ────────────────────────────────────────────────────────

def _fda_walkthrough(app_path: Path) -> None:
    """Open System Settings to the Full Disk Access pane and walk the
    user through adding the .app. We can't programmatically grant TCC
    permissions — that's the whole point of TCC — so this is the best
    we can do."""
    bold("Full Disk Access grant")
    out("The backup script reads from your stacklet data directories and")
    out("writes to the external vault disk. Both need Full Disk Access")
    out("when the script runs from cron (a sandboxed context).")
    nl()
    out("Steps:")
    out(f"  1. Settings opens to the Full Disk Access pane.")
    out(f"  2. Click + to add an app.")
    out(f"  3. Press {bold_text('⌘⇧G')} and paste:")
    out(f"       {app_path}")
    out(f"  4. Select {bold_text('FamstackVaultSync.app')} and turn it on.")
    nl()

    # Deep-link to the FDA pane. This URL works on macOS 13+. Older
    # macOS opens to the general Privacy pane.
    subprocess.run([
        "open",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
    ], check=False)

    if not confirm("Done? (you don't have to relaunch anything)", default=True):
        warn("Skipping FDA confirmation — backups may fail until granted.")
        dim("  You can run 'stack up backup' again later to re-trigger this prompt.")
    nl()


def bold_text(s: str) -> str:
    """Inline bold wrapping. Helper because ``stack.prompt.bold`` prints
    on its own line; we need inline emphasis."""
    from stack.prompt import BOLD, RESET
    return f"{BOLD}{s}{RESET}"
