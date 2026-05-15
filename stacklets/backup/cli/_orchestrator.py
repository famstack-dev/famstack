"""Backup orchestrator helpers — discovery, invocation, formatting.

The orchestrator's job is to coordinate the engine. The engine knows
how to back up; the orchestrator knows *what* to back up and *where to
report* the outcome.

Pipeline:

  1. Read ``[backup.targets.*]`` from stack.toml.
  2. Walk enabled stacklets for ``[[backup.archive]]`` entries.
  3. Render template variables in the declared paths.
  4. For each target: build the ``$SOURCES`` env string, invoke the
     engine, read the latest entry from ``history.jsonl``, format and
     post a Matrix summary.

This module holds the pure functions so they can be unit-tested
without running rsync, diskutil, or Matrix logins. The entry point
``cli/sync.py`` is a thin wrapper that wires them up.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover — py < 3.11 fallback
    from stack._vendor import tomli as tomllib  # type: ignore


# ── Domain types ───────────────────────────────────────────────────────────

@dataclass
class SourceRecord:
    """One ``[[backup.archive]]`` entry, after template rendering.

    These are the records the engine consumes. The orchestrator builds
    them by discovery; the engine never reads stacklet manifests
    itself.
    """

    id: str            # "{stacklet_id}/{archive.name}", e.g. "photos/library"
    display: str       # Human-readable, e.g. "Photos"
    src_path: Path     # Absolute path on internal SSD (post-rendering)
    vault_subdir: str  # Relative path under /Volumes/<vault>/
    min_files: int     # Coarse ransomware guard threshold


@dataclass
class Target:
    """One ``[backup.targets.<name>]`` block from stack.toml."""

    name: str        # User-chosen label ("vault", "offsite", ...)
    engine: str      # "external-disk" (today), "restic" (future)
    disk: str        # Volume name; meaningful for external-disk
    schedule: str    # Cron expression; informational at this level


# ── Source discovery ───────────────────────────────────────────────────────

def discover_archive_sources(
    repo_root: Path,
    instance_dir: Path,
    data_dir: Path,
) -> List[SourceRecord]:
    """Walk every ``stacklets/*/stacklet.toml`` and gather
    ``[[backup.archive]]`` entries.

    A stacklet contributes only if it's enabled — presence of
    ``.stack/{id}.setup-done`` under ``instance_dir``. Template vars
    (currently just ``{data_dir}``) are rendered into the path field.

    The source ``id`` is ``{stacklet_id}/{archive.name}`` so a single
    stacklet can declare multiple archives without collision. The
    vault subdirectory is derived as ``data/{stacklet_id}-{name}`` —
    short, stable, and namespaced so future stacklets can't accidentally
    clobber existing archive directories.
    """
    stacklets_dir = repo_root / "stacklets"
    if not stacklets_dir.is_dir():
        return []

    template_vars = {"data_dir": str(data_dir)}
    sources: List[SourceRecord] = []

    for manifest_path in sorted(stacklets_dir.glob("*/stacklet.toml")):
        stacklet_id = manifest_path.parent.name
        if not _is_setup_done(instance_dir, stacklet_id):
            continue

        manifest = _safe_load_toml(manifest_path)
        if manifest is None:
            continue

        archives = manifest.get("backup", {}).get("archive", [])
        if not archives:
            continue

        stacklet_display = manifest.get("name", stacklet_id)

        for archive in archives:
            name = archive.get("name", "default")
            raw_path = archive.get("path", "")
            try:
                rendered_path = raw_path.format(**template_vars)
            except (KeyError, IndexError):
                # An unrecognized template variable. Treat the raw
                # string as the literal path; the engine's preflight
                # will surface the problem with a useful error.
                rendered_path = raw_path

            try:
                min_files = int(archive.get("min_files", 1))
            except (TypeError, ValueError):
                min_files = 1

            sources.append(SourceRecord(
                id=f"{stacklet_id}/{name}",
                display=stacklet_display,
                src_path=Path(rendered_path),
                vault_subdir=f"data/{stacklet_id}-{name}",
                min_files=min_files,
            ))

    return sources


def _is_setup_done(instance_dir: Path, stacklet_id: str) -> bool:
    """Replicates Stack._is_set_up without importing Stack — keeps this
    module standalone-testable."""
    return (instance_dir / ".stack" / f"{stacklet_id}.setup-done").exists()


def _safe_load_toml(path: Path) -> Optional[dict]:
    """Load TOML, returning None on read or parse failure. A single
    broken manifest shouldn't take down the whole sync."""
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        return None


# ── Target discovery ───────────────────────────────────────────────────────

def get_targets(stack_config: dict) -> List[Target]:
    """Parse ``[backup.targets.*]`` from stack.toml. Returns empty list
    when the section is missing or contains nothing usable.

    Entries lacking ``engine`` are skipped — they're malformed and
    shouldn't silently behave like a default.
    """
    targets_cfg = stack_config.get("backup", {}).get("targets", {})
    targets: List[Target] = []
    for name, cfg in targets_cfg.items():
        if not isinstance(cfg, dict):
            continue
        engine = cfg.get("engine", "")
        if not engine:
            continue
        targets.append(Target(
            name=name,
            engine=engine,
            disk=cfg.get("disk", ""),
            schedule=cfg.get("schedule", ""),
        ))
    return targets


# ── Engine invocation ──────────────────────────────────────────────────────

def serialize_sources_env(sources: List[SourceRecord]) -> str:
    """Format SourceRecords for the engine's ``$SOURCES`` env var.

    The engine's :func:`parse_sources` expects newline-separated,
    pipe-delimited records: ``id|display|src_path|vault_subdir|min_files``.
    """
    return "\n".join(
        f"{s.id}|{s.display}|{s.src_path}|{s.vault_subdir}|{s.min_files}"
        for s in sources
    )


def build_engine_command(
    engine_script: Path,
    args: argparse.Namespace,
) -> List[str]:
    """Compose the engine subprocess command line."""
    cmd = [sys.executable, str(engine_script)]
    if getattr(args, "dry_run", False):
        cmd.append("--dry-run")
    if getattr(args, "no_eject", False):
        cmd.append("--no-eject")
    if getattr(args, "verbose", False):
        cmd.append("--verbose")
    if getattr(args, "verify", False):
        cmd.append("--verify")
    return cmd


def invoke_engine(
    engine_script: Path,
    backup_data_dir: Path,
    target: Target,
    sources: List[SourceRecord],
    args: argparse.Namespace,
) -> int:
    """Run the engine for one target. Returns the engine's exit code.

    The engine appends to ``history.jsonl`` under
    ``BACKUP_DATA_DIR/logs/``; callers read the last entry via
    :func:`read_latest_run` after this returns.

    The canary is planted by ``on_install``, not by the engine on
    first run, so the orchestrator doesn't need to pass any extra
    state for that — the engine simply verifies the canary it expects
    to find.
    """
    env = os.environ.copy()
    env["BACKUP_DATA_DIR"] = str(backup_data_dir)
    env["VAULT_DISK"] = target.disk
    env["SOURCES"] = serialize_sources_env(sources)

    cmd = build_engine_command(engine_script, args)
    result = subprocess.run(cmd, env=env)
    return result.returncode


# ── Result reading ─────────────────────────────────────────────────────────

def read_latest_run(backup_data_dir: Path) -> Optional[dict]:
    """Return the most recent run's outcome by tail-scanning
    ``history.jsonl``.

    Returns ``None`` when the file is missing or contains no parseable
    line. The caller treats that case as "the engine crashed before it
    could report" — distinct from a written failure result.

    Tolerates a corrupted trailing line (e.g. a partial write from a
    crashed engine, though our writes fit under PIPE_BUF and should be
    atomic). Walks past unparseable lines and returns the last good
    one.
    """
    path = backup_data_dir / "logs" / "history.jsonl"
    if not path.exists():
        return None
    latest: Optional[dict] = None
    try:
        with path.open() as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    latest = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None
    return latest


# ── Notification formatting ────────────────────────────────────────────────

def format_notification(target_name: str, result: dict) -> Tuple[str, str]:
    """Build ``(plain_text, html)`` Matrix bodies for one target's result.

    The plain version is the fallback for text-only clients. The HTML
    version bolds the numbers that matter (totals, new files) so
    Element renders them readably.
    """
    headline_plain, headline_html = _headline(result)
    state_plain, state_html = _vault_state_line(result)
    source_lines_plain, source_lines_html = _source_lines(result)

    duration_str = _duration(result.get("duration_seconds", 0))
    vault_size = result.get("vault_size", "unknown")
    run_user = result.get("run_user") or "unknown"
    run_context = result.get("run_context", "unknown")

    failure_line_plain = ""
    failure_line_html = ""
    if not result.get("success") and result.get("failure_reason"):
        failure_line_plain = f"Reason: {result['failure_reason']}\n"
        failure_line_html = f"Reason: {result['failure_reason']}<br>"

    plain = (
        f"{headline_plain}\n"
        f"{failure_line_plain}"
        f"\n"
        f"Target: {target_name}\n"
        f"Duration: {duration_str} | Backup size: {vault_size}\n"
        f"Run by: {run_user} via {run_context}\n"
        f"\n"
        + ("\n".join(source_lines_plain) + "\n" if source_lines_plain else "")
        + f"\n{state_plain}"
    )
    html = (
        f"{headline_html}<br>"
        f"{failure_line_html}"
        f"<br>"
        f"<b>Target:</b> {target_name}<br>"
        f"<b>Duration:</b> {duration_str} &nbsp;|&nbsp; "
        f"<b>Backup size:</b> {vault_size}<br>"
        f"<b>Run by:</b> {run_user} via {run_context}<br>"
        f"<br>"
        + ("<br>".join(source_lines_html) + "<br>" if source_lines_html else "")
        + f"<br>{state_html}"
    )
    return plain, html


def _headline(result: dict) -> Tuple[str, str]:
    if result.get("dry_run"):
        return "🧪 Backup Sync (dry run)", "<b>🧪 Backup Sync (dry run)</b>"
    if result.get("success"):
        return "✅ Backup Sync Completed", "<b>✅ Backup Sync Completed</b>"
    return "❌ Backup Sync FAILED", "<b>❌ Backup Sync FAILED</b>"


def _vault_state_line(result: dict) -> Tuple[str, str]:
    state = result.get("vault_state", "unknown")
    run_context = result.get("run_context", "unknown")
    if state == "not_connected":
        msg = "⚠️ Backup disk not connected."
    elif state == "mounted":
        # Document the operational truth: scheduled syncs can't eject,
        # so a mounted disk after a cron run isn't a problem — it's the
        # expected steady state.
        msg = f"ℹ️ Backup disk mounted (eject not available from {run_context})."
    elif state == "ejected":
        msg = "⏏️ Backup disk ejected."
    else:
        msg = f"Backup disk state: {state}"
    return msg, msg


def _source_lines(result: dict) -> Tuple[List[str], List[str]]:
    plain: List[str] = []
    html: List[str] = []
    for src in result.get("sources", []):
        emoji = _source_emoji(src.get("id", ""))
        display = src.get("display", "Source")
        status = src.get("status", "skipped")
        total = src.get("total_files", 0)
        new = src.get("new_files", 0)

        if status == "ok":
            plain.append(
                f"{emoji} {display} — {_format_number(total)} files "
                f"({_format_number(new)} new)"
            )
            html.append(
                f"{emoji} {display} — <b>{_format_number(total)}</b> files "
                f"(<b>{_format_number(new)}</b> new)"
            )
        elif status == "FAILED":
            plain.append(f"{emoji} {display} — FAILED")
            html.append(f"{emoji} {display} — <b>FAILED</b>")
        else:
            plain.append(f"{emoji} {display} — skipped")
            html.append(f"{emoji} {display} — skipped")

    return plain, html


def _source_emoji(source_id: str) -> str:
    """Decorative emoji for a source family. Pure cosmetics — no
    semantics depend on these."""
    if source_id.startswith("photos/"):
        return "📷"
    if source_id.startswith("docs/"):
        return "📄"
    if source_id.startswith("code/"):
        return "💾"
    return "📦"


def _duration(seconds: int) -> str:
    """Format duration as ``Xm Ys``."""
    mins, secs = divmod(int(seconds), 60)
    return f"{mins}m {secs}s"


def _format_number(n: int) -> str:
    """Dot-separated thousands. Mirrors the engine's helper so notification
    numbers match the engine's terminal output; comma triggers phone-number
    linkification in Element."""
    s = str(n)
    if len(s) <= 3:
        return s
    parts: List[str] = []
    while len(s) > 3:
        parts.insert(0, s[-3:])
        s = s[:-3]
    parts.insert(0, s)
    return ".".join(parts)
