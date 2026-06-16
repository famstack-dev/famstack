"""stack backup status — show the last run and current target health.

Reports per configured target:
  - When it last synced and whether it succeeded (from the recorded run
    in ``history.jsonl`` — no live file walk)
  - Per-source file counts as of that run
  - Whether the canary tripwire is intact
  - Whether the cron entry for the next scheduled run is wired
  - Whether the disk is currently mounted

A mounted disk is the steady-state expectation for scheduled mode —
eject is sandbox-blocked from cron, so the disk stays mounted between
runs and files are kernel-immutable. "Not mounted" is only a problem
when the disk is encrypted and hasn't been unlocked since reboot, is
physically disconnected, or the previous scheduled run failed to find
it — so we surface it as information, not an error.

Counts come from the last recorded run rather than re-walking the
source and vault trees: a status command shouldn't stat tens of
thousands of files. The numbers are "as of the last sync," which is
what a health check wants anyway.

Outputs JSON when piped, human-readable otherwise. Matches the
convention of `stack status`, `stack errors`, `stack host`.
"""

HELP = "Show last-run status and target health"

import json
import sys
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
from _orchestrator import get_targets, read_latest_run  # noqa: E402

# CANARY_STRING + format_number live in the engine — single source of
# truth for the tripwire content and the dot-thousands formatting the
# rest of the stacklet uses.
_engine_dir = _here.parent / "engines" / "external-disk"
sys.path.insert(0, str(_engine_dir))
from sync import CANARY_STRING, format_number  # noqa: E402

sys.path.insert(0, str(_here.parent))
import _cron as cron  # noqa: E402


# ── Health probes ──────────────────────────────────────────────────────────

def _canary_state(backup_data_dir: Path) -> str:
    """``"intact"`` | ``"missing"`` | ``"tampered"`` for the tripwire."""
    canary = backup_data_dir / "canary"
    if not canary.exists():
        return "missing"
    try:
        content = canary.read_text().strip()
    except OSError:
        return "missing"
    return "intact" if content == CANARY_STRING else "tampered"


def _target_status(target, backup_data_dir: Path) -> dict:
    """Assemble one target's health snapshot. Pure reads — no sync."""
    mount_point = Path("/Volumes") / target.disk
    return {
        "name": target.name,
        "engine": target.engine,
        "disk": target.disk,
        "schedule": target.schedule,
        "disk_mounted": mount_point.is_dir(),
        "cron_installed": cron.is_installed(target.name),
        "canary": _canary_state(backup_data_dir),
        "last_run": read_latest_run(backup_data_dir, target.disk),
    }


# ── Backup data dir resolution (mirrors cli/sync.py) ───────────────────────

def _resolve_backup_data_dir(config: dict) -> Path:
    manifest = config.get("manifest", {})
    template = manifest.get("env", {}).get("defaults", {}).get(
        "BACKUP_DATA_DIR", "{data_dir}/backup"
    )
    data_dir = config.get("data_dir", "")
    try:
        return Path(template.format(data_dir=data_dir))
    except (KeyError, IndexError):
        return Path(data_dir) / "backup"


# ── Human rendering ────────────────────────────────────────────────────────

def _render_human(targets: list) -> None:
    from stack.prompt import bold, dim, done, nl, out, section, warn

    section("Backup status", f"{len(targets)} configured target"
            f"{'' if len(targets) == 1 else 's'}")

    for t in targets:
        nl()
        bold(f"Target '{t['name']}'  ({t['engine']} → {t['disk']})")

        run = t["last_run"]
        if run is None:
            dim("  Last run:   never (no recorded sync yet)")
        else:
            outcome = "✅ success" if run.get("success") else "❌ FAILED"
            when = run.get("ended_at") or run.get("started_at") or "unknown time"
            dur = _duration(run.get("duration_seconds", 0))
            out(f"  Last run:   {when} - {outcome} ({dur})")
            if not run.get("success") and run.get("failure_reason"):
                warn(f"  Reason:     {run['failure_reason']}")

        # Disk + cron + canary lines.
        out(f"  Disk:       {'mounted' if t['disk_mounted'] else 'not mounted'}")
        out(f"  Cron:       {'installed' if t['cron_installed'] else 'NOT installed'}"
            f"  ({t['schedule'] or 'no schedule'})")
        canary = t["canary"]
        if canary == "intact":
            done("Canary:     intact")
        elif canary == "missing":
            warn("  Canary:     MISSING - run 'stack up backup' to replant")
        else:
            warn("  Canary:     TAMPERED - possible ransomware; investigate before syncing")

        # Per-source counts from the recorded run.
        sources = (run or {}).get("sources", [])
        if sources:
            out("  Sources:")
            for s in sources:
                total = format_number(s.get("total_files", 0))
                new = format_number(s.get("new_files", 0))
                status = s.get("status", "skipped")
                tag = "" if status == "ok" else f" [{status}]"
                out(f"    {s.get('display', 'Source')}: {total} files "
                    f"({new} new last run){tag}")

        if run is not None:
            out(f"  Vault size: {run.get('vault_size', 'unknown')}")
    nl()


def _duration(seconds) -> str:
    mins, secs = divmod(int(seconds or 0), 60)
    return f"{mins}m {secs}s"


# ── Entry point ────────────────────────────────────────────────────────────

def run(args, stacklet, config):
    """Entry point invoked by the framework via ``stack backup status``."""
    backup_data_dir = _resolve_backup_data_dir(config)
    targets = get_targets(config.get("stack", {}))

    if not targets:
        return {
            "error": "No backup targets configured. Add a [backup.targets.<name>] "
                     "block to stack.toml (see stack.example.toml)."
        }

    snapshots = [_target_status(t, backup_data_dir) for t in targets]

    if sys.stdout.isatty():
        _render_human(snapshots)
    else:
        print(json.dumps({"targets": snapshots}, indent=2))

    return {"ok": True, "targets": [s["name"] for s in snapshots]}
