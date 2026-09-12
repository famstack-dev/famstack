"""stack backup migrate — move a vault to the current directory layout.

Vaults written with 0.3.0-beta.3 or earlier hold one flat directory per
source: ``data/messages-synapse``. Current releases nest them under the
stacklet, ``data/messages/synapse``, so everything belonging to one
stacklet restores from one place and a hyphen inside a stacklet id or a
source name stays unambiguous.

Both layouts are read. A sync keeps using whichever flat directory it
finds, so an un-migrated vault goes on working and no file is ever
copied twice. This command performs the one-time rename.

What moves is the directory, not its contents: the files inside keep the
immutable flag that makes the vault append-only, and nothing is copied.
The disk must already be mounted, because unlocking and mounting it is
``stack backup sync``'s job.

Usage:
  stack backup migrate            rename legacy directories on every target
  stack backup migrate --dry-run  list what would be renamed
"""

HELP = "Move a vault's data directories to the current layout"

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
from _orchestrator import (  # noqa: E402
    discover_archive_sources,
    get_targets,
    legacy_vault_subdir,
    vault_subdir,
)
from _snapshot import discover_snapshots  # noqa: E402


@dataclass
class Move:
    """One source's directory on one vault, and what became of it."""

    display: str
    old: Path
    new: Path
    status: str        # "moved" | "occupied" | "failed"
    reason: str = ""


# ── Planning ───────────────────────────────────────────────────────────────

def declared_sources(
    repo_root: Path, instance_dir: Path, data_dir: Path,
) -> List[Tuple[str, str]]:
    """Every source that could own a directory on a vault, as (id, display).

    Archives and snapshots both land in ``data/`` under their source id,
    so both are candidates. A stacklet that has since been disabled is
    not discovered and keeps its flat directory, which stays readable.
    """
    found = {
        s.id: s.display
        for s in discover_archive_sources(repo_root, instance_dir, data_dir)
    }
    for spec in discover_snapshots(repo_root, instance_dir, data_dir):
        found.setdefault(spec.id, spec.display)
    return sorted(found.items())


def migrate_vault(
    mount_point: Path, sources: List[Tuple[str, str]], *, dry_run: bool,
) -> List[Move]:
    """Rename each legacy directory found on this vault, and report.

    A source whose new directory already exists is left alone. That
    means both layouts hold data, which no rename can reconcile: the
    files are immutable, so they cannot be merged into one tree without
    unlocking them, and choosing one would hide the other.
    """
    moves: List[Move] = []

    for source_id, display in sources:
        old = mount_point / legacy_vault_subdir(source_id)
        new = mount_point / vault_subdir(source_id)
        if not old.is_dir():
            continue

        if new.exists():
            moves.append(Move(display, old, new, "occupied"))
            continue

        if not dry_run:
            try:
                new.parent.mkdir(parents=True, exist_ok=True)
                old.rename(new)
            except OSError as e:
                moves.append(Move(display, old, new, "failed", str(e)))
                continue

        moves.append(Move(display, old, new, "moved"))

    return moves


# ── Output ─────────────────────────────────────────────────────────────────

def _render(target_name: str, moves: List[Move], dry_run: bool) -> None:
    from stack.prompt import bold, dim, done, error, nl, warn

    nl()
    bold(f"Target '{target_name}'")
    if not moves:
        dim("Already on the current layout.")
        return

    for m in moves:
        nested = f"{m.new.parent.name}/{m.new.name}"
        if m.status == "moved":
            verb = "would move" if dry_run else "moved"
            done(f"{m.display}: {verb} {m.old.name} to {nested}")
        elif m.status == "occupied":
            warn(f"{m.display}: both {m.old.name} and {nested} hold data. "
                 "Merge them by hand, then re-run.")
        else:
            error(f"{m.display}: could not move {m.old.name} ({m.reason})")


# ── Entry point ────────────────────────────────────────────────────────────

def _parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="stack backup migrate",
                                     description=HELP)
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be renamed (no changes).")
    return parser.parse_args(argv)


def run(args, stacklet, config):
    """Entry point invoked by the framework via ``stack backup migrate``."""
    parsed = _parse_args(args or [])

    repo_root = Path(config.get("repo_root", "."))
    instance_dir = Path(config.get("instance_dir", repo_root))
    data_dir = Path(config.get("data_dir", "."))

    targets = get_targets(config.get("stack", {}))
    if not targets:
        return {
            "error": "No backup targets configured. Add a [backup.targets.<name>] "
                     "block to stack.toml (see stack.example.toml)."
        }

    sources = declared_sources(repo_root, instance_dir, data_dir)
    unreachable: List[str] = []
    problems = 0
    migrated = 0

    for target in targets:
        mount_point = Path("/Volumes") / target.disk
        if not mount_point.is_dir():
            unreachable.append(target.name)
            continue

        moves = migrate_vault(mount_point, sources, dry_run=parsed.dry_run)
        _render(target.name, moves, parsed.dry_run)
        migrated += sum(1 for m in moves if m.status == "moved")
        problems += sum(1 for m in moves if m.status != "moved")

    if unreachable and len(unreachable) == len(targets):
        return {
            "error": "No backup disk is mounted. Connect it and run "
                     "'stack backup sync' once, which unlocks and mounts it."
        }
    for name in unreachable:
        print(f"  [{name}] disk not mounted, skipped", file=sys.stderr)

    if problems:
        return {"error": "Some directories could not be moved; see above"}
    return {"ok": True, "moved": migrated, "dry_run": parsed.dry_run}
