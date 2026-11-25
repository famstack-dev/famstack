"""The command line of `stack rollback`.

    stack rollback [<snapshot>] [--yes]        back to the snapshot before the last update

    rollback_cli.add_parser(sub)    # in lib/stack/cli.py, next to the other commands
    rollback_cli.run(stck, args, lifecycle)    # what `stack rollback ...` runs

`stack rollback` shows its plan (`stack.snapshot.rollback`): the
snapshot and the release that the rollback goes back to. The plan also
names the stacklets that the rollback replaces and the files that came
in after the snapshot. The command waits for a confirmation, or `--yes`
where there is no terminal. Then the rollback runs, and the old release
starts its stacklets with a `stack up` process of its own:

    $ stack rollback
      Roll back to 0.4.0-rc.1, the snapshot 2026-11-25T2114Z (2026-11-25 22:14)
      Replaces docs, photos. Their changes since then move to
      .snapshots/replaced/, and the stacklets lose them.
      ? Roll back? [y/N]

While it asks, it holds the run locks of the plan's stacklets as the
action `rollback` (`runs.reserve`). A start of one of them refuses
meanwhile. `stack snapshot remove` refuses too, because the plan
restores one of the snapshots (`stack.snapshot.removal`).

A question left open keeps those locks: the debt is in
`stack.snapshot.answer`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .. import runs
from ..prompt import DIM, GREEN, ORANGE, RESET
from . import rollback, store
from .answer import ask, print_answer
from .errors import SnapshotError
from .lifecycle import Lifecycle
from .rollback_record import RUN_ACTION
from .volume import Volume

__all__ = ["add_parser", "run"]

# A photo library can get hundreds of files after a snapshot. The text
# names this many, and `--json` gives every path.
NEWER_SHOWN = 20


def add_parser(sub) -> None:
    """Add `stack rollback` and its options to `sub`, the CLI's subparsers."""
    p = sub.add_parser("rollback")
    p.add_argument("snapshot", nargs="?", default=None,
                   help="the snapshot to go back to (default: the newest)")
    p.add_argument("--yes", action="store_true", help="Skip the confirmation")
    p.add_argument("--json", action="store_true", help="Output as JSON")


def run(stck, args, lifecycle: Lifecycle, volume: Volume | None = None) -> None:
    """`stack rollback [<snapshot>]` on the instance `stck`: show the
    plan, wait for the confirmation, run it. Prints the answer. Exits 1
    when it refused or a step failed. A declined confirmation changes
    nothing and exits 0. With `--json` it prints the plan as JSON, and the
    question goes to stderr. Without a terminal it needs `--yes`.

    `lifecycle.down` stops the stacklets it replaces. `volume` is the file
    system of the data folder, `volume.ThisVolume` by default.

        $ stack rollback --yes --json
        {"ok": true, "snapshot": "2026-11-25T2114Z", "version": "0.4.0-rc.1",
         "replaced": ".../.snapshots/replaced/2026-11-25T2203Z", "started": ["docs"],
         "newer_files": ["docs/paperless/media/new-bill.pdf"],
         "data_folder": "/Users/homer/famstack-data", "tags": [], ...}
    """
    try:
        planned = rollback.plan(stck, args.snapshot)
    except rollback.RollbackError as error:
        return print_answer({"error": str(error), "problems": error.problems}, args.json, None)
    if not args.json:
        print("\n".join(_plan_lines(planned, stck.data)))
    if not args.yes:
        if not sys.stdin.isatty():
            return print_answer({"error": "Pass --yes to confirm (no terminal to ask in)"},
                                args.json, None)
        try:
            confirmed = _confirmed(stck, planned, quiet=args.json)
        except runs.AlreadyRunning as busy:
            return print_answer(busy.as_result(), args.json, None)
        if not confirmed:
            return print_answer({"ok": False, "declined": True, **_planned(stck, planned)}, args.json,
                                lambda _: [f"  {DIM}Nothing changed.{RESET}", ""])
    try:
        done = rollback.run(stck, planned, stop=lifecycle.down,
                            start=_starting(stck, quiet=args.json), volume=volume)
    except SnapshotError as error:
        return print_answer({"error": str(error), "problems": getattr(error, "problems", [])},
                            args.json, None)
    print_answer({**_planned(stck, planned), "ok": True, "replaced": str(done.replaced),
                  "started": done.started, "cloned": done.cloned, "moved_tags": done.tags},
                 args.json, _rolled_back_lines)


# ── Rolling back ─────────────────────────────────────────────────────────

def _starting(stck, quiet: bool):
    """The rollback's start: `stack up <ids>` as a process of its own,
    from the checkout. By then the checkout is on the snapshot's commit.
    The process runs that release's code and framework, not the code
    this process loaded. Its output goes to the terminal, or to stderr
    with `--json` (`quiet`). Says whether it exited 0."""
    def start(ids: list[str]) -> bool:
        if not ids:
            return True
        # The checkout's lib/ first: the wrapper `./stack` sets the same.
        paths = [str(Path(stck.root) / "lib"), os.environ.get("PYTHONPATH", "")]
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in paths if p)}
        done = subprocess.run([sys.executable, "-m", "stack", "up", *ids], cwd=stck.root,
                              env=env, stdout=sys.stderr if quiet else None)
        return done.returncode == 0
    return start


def _confirmed(stck, planned: rollback.Plan, quiet: bool) -> bool:
    """Ask the confirmation, on stderr when stdout carries JSON (`quiet`).

    Holds the run locks of the plan's stacklets while it asks, as the
    action `rollback`. It holds them because a `stack snapshot remove`
    meanwhile must refuse the snapshot that the plan restores. Raises
    `runs.AlreadyRunning` when a run of one of them goes on. The locks
    go before the run, which takes them again with its own run log."""
    with runs.reserve(stck.instance_dir, planned.stacklets, action=RUN_ACTION):
        return ask("Finish the rollback?" if planned.replaced else "Roll back?", quiet)


def _planned(stck, planned: rollback.Plan) -> dict:
    """The plan, as the answer carries it. `data_folder` is the folder
    that the paths of `newer_files` are under."""
    return {"snapshot": planned.snapshot.name, "version": planned.version,
            "stacklets": list(planned.stacklets), "starts": planned.starts,
            "newer_files": planned.newer, "data_folder": str(stck.data),
            "others": planned.others,
            "tags": [{"name": t.name, "image": t.image_id, "now": t.now,
                      "also_used_by": list(t.also_used_by)} for t in planned.tags],
            "finishes": planned.replaced.name if planned.replaced else None,
            "left": planned.left}


def _plan_lines(planned: rollback.Plan, data: Path) -> list[str]:
    """What a person reads before the confirmation. `data` is the data
    folder."""
    if planned.replaced is not None:
        lines = ["", f"  {ORANGE}⚠{RESET} The rollback of {planned.replaced.name} to "
                     f"{planned.version} (snapshot {planned.snapshot.name}) stopped halfway.",
                 f"  This finishes it. Left: {'; '.join(planned.left)}.",
                 f"  {DIM}The state it replaced: {planned.replaced}{RESET}"]
    else:
        local = planned.snapshot.taken.astimezone().strftime("%Y-%m-%d %H:%M")
        lines = ["", f"  Roll back to {planned.version}, the snapshot {planned.snapshot.name} "
                     f"({local})",
                 f"  Replaces {', '.join(planned.stacklets)}. Their changes since then move to "
                 f"{store.SNAPSHOTS_FOLDER}/{rollback.REPLACED_FOLDER}/,",
                 "  and the stacklets lose them."]
    lines += _newer_lines(planned.newer, data) + _tag_lines(planned.tags)
    if planned.others:
        lines.append(f"  {DIM}Not in the snapshot, still running: {', '.join(planned.others)}. "
                     f"They keep the newer code until they restart.{RESET}")
    return [*lines, ""]


def _newer_lines(newer: list[str], data: Path | str) -> list[str]:
    """The files newer than the snapshot, by their paths under the data
    folder `data`, newest first: one warning that names the folder, then
    at most `NEWER_SHOWN` paths and how many more there are.

        ⚠ 1 file came after the snapshot. It stays, and the restored
          state does not know it. Under the data folder /Users/homer/famstack-data:
            docs/paperless/media/new-bill.pdf
    """
    if not newer:
        return []
    one = len(newer) == 1
    lines = [f"  {ORANGE}⚠{RESET} {len(newer)} file{'' if one else 's'} came after the "
             f"snapshot. {'It stays' if one else 'They stay'}, and the restored state does "
             f"not know {'it' if one else 'them'}. Under the data folder {data}:"]
    lines += [f"      {path}" for path in newer[:NEWER_SHOWN]]
    if len(newer) > NEWER_SHOWN:
        lines.append(f"      and {len(newer) - NEWER_SHOWN} more")
    return lines


def _tag_lines(tags: list) -> list[str]:
    """One line for each tag the rollback moves back, and one for the
    stacklets outside the rollback that start it too."""
    lines = []
    for tag in tags:
        lines.append(f"  The tag {tag.name} names the image {tag.image_id[:19]} again.")
        if tag.also_used_by:
            lines.append(f"    {ORANGE}⚠{RESET} {', '.join(tag.also_used_by)} start "
                         f"this tag too, and get that image at the next start.")
    return lines


def _rolled_back_lines(result: dict) -> list[str]:
    """What a person reads after the rollback."""
    lines = [f"\n  {GREEN}✓{RESET}  Rolled back to {result['version']} "
             f"(snapshot {result['snapshot']}): {', '.join(result['stacklets'])}",
             f"     {DIM}The state it replaced: {result['replaced']}{RESET}"]
    lines += [f"     {DIM}The tag {name} names the snapshot's image again.{RESET}"
              for name in result["moved_tags"]]
    lines += _newer_lines(result["newer_files"], result["data_folder"])
    return [*lines, ""]
