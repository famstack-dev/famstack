"""The snapshot of a restart, as `stack restart` and `stack up` call it.

The restart that applies a release (`stack restart` in `stack.cli`) takes
its snapshot in three calls around its own stop and start. They print
what the restart says and exit 1 when no snapshot was taken. The
decisions are in `stack.snapshot.release`. This module holds the text.
A stacklet whose take failed waits for its snapshot, and
`stack.snapshot.guard` refuses its start until a restart takes it:

    from stack.snapshot import restart_cli

    before = restart_cli.record_or_exit(stck, ["docs"], no_snapshot=False)   # while docs runs
    cli.down_many(["docs"])
    taken = restart_cli.take_or_exit(stck, before)        # nothing starts without it
    cli.up_many(["docs"])
    restart_cli.keep_and_say(stck, taken)                 # the newest 3 stay

`plan_line` is the line of `stack update`'s plan that names the snapshot.
"""

from __future__ import annotations

import sys
from typing import NoReturn

from .. import run_log
from ..prompt import DIM, GREEN, ORANGE, RED, RESET
from . import pending, release, store
from .errors import SnapshotError
from .release import Before
from .store import Taken

__all__ = ["keep_and_say", "plan_line", "problem_line", "record_or_exit", "stopped_or_exit",
           "take_or_exit"]


def record_or_exit(stck, ids: list[str], no_snapshot: bool) -> Before:
    """`release.before_stop` for a restart of `ids`, before it stops them.
    `no_snapshot` is `stack restart --no-snapshot`. Exits 1 with the cause
    and the two ways on when the record fails. In that case nothing has
    stopped.
    Names each stacklet whose commit is unknown, which gets no snapshot.

        record_or_exit(stck, ["docs"], no_snapshot=False)   # Before(stacklets={"docs": …}, …)
    """
    try:
        before = release.before_stop(stck, ids, skip=no_snapshot)
    except pending.Unreadable as error:
        _exit(stck, [error.stacklet], str(error), _unreadable_lines(error))
    except SnapshotError as error:
        _exit(stck, ids, str(error), _refused_lines(str(error), [], ids))
    if before.unknown:
        print("\n".join(_unknown_lines(before.unknown)), file=sys.stderr)
    return before


def take_or_exit(stck, before: Before) -> Taken | None:
    """`release.after_stop` between the stop and the start of the
    stacklets `before` recorded: the snapshot, or None when `before` is
    empty. Exits 1 with the cause and the two ways on when the take
    fails. In that case they stay stopped and wait for their snapshot
    (`stack.snapshot.guard.waiting`).

        take_or_exit(stck, before).snapshot.name   # "2026-11-25T2114Z"
    """
    stacklets = list(before.stacklets)
    try:
        return release.after_stop(stck, before)
    except release.StillRunning as error:
        waits = [sid for sid in stacklets if sid not in error.ids]
        _exit(stck, stacklets, str(error), _refused_lines(str(error), waits, error.ids))
    except SnapshotError as error:
        _exit(stck, stacklets, str(error), _refused_lines(str(error), stacklets, []))


def stopped_or_exit(stck, held: list[str], down_result: dict) -> None:
    """Return when the stop of a restart stopped every stacklet. Else
    record the stop's errors as the result of each run of `held`. Then
    print them and exit 1. A start would only be refused
    (`stack.snapshot.guard`), because a stacklet that did not stop still
    runs its release. The refusal would name the restart just run as its
    way on.

        stopped_or_exit(stck, ["docs"], {"ok": False, "errors": [
            {"stacklet": "docs", "result": {"error": "container is busy"}}]})
        #   ✗  Some stacklets failed to stop
        #
        #       docs: container is busy
    """
    if down_result.get("ok"):
        return
    error = down_result.get("error") or "Some stacklets failed to stop"
    problems = [problem_line(e) for e in down_result.get("errors", [])]
    for sid in held:
        run_log.record_result(stck.instance_dir, sid,
                              {"error": error, "problems": problems, "stacklet": sid})
    # The layout of `stack.cli.print_error`: the error, a blank line, then
    # one indented line per stacklet.
    print(f"\n  {RED}✗{RESET}  {error}\n", file=sys.stderr)
    for problem in problems:
        print(f"      {problem}", file=sys.stderr)
    sys.exit(1)


def problem_line(failed: dict) -> str:
    """One stacklet's line of a failed stop or start: its id, and its
    error if any. `stack up` of several stacklets prints the same lines.

        problem_line({"stacklet": "docs", "result": {"error": "container is busy"}})
        # "docs: container is busy"
    """
    error = failed.get("result", {}).get("error")
    return f"{failed['stacklet']}: {error}" if error else failed["stacklet"]


def keep_and_say(stck, taken: Taken | None) -> None:
    """After a restart that took the snapshot `taken`: keep the newest
    snapshots (`release.prune`) and print what was taken and removed.
    Says nothing without a snapshot.

        keep_and_say(stck, taken)
        #   ✓ Snapshot 2026-11-25T2114Z: docs
        #     Removed the snapshot 2026-11-01T0900Z; the newest 3 stay.
    """
    if taken is not None:
        print("\n".join(_taken_lines(taken, release.prune(stck.data, taken.snapshot.name))))


def plan_line() -> str:
    """The line of `stack update`'s plan that names the snapshot, under
    "Needs a restart afterwards: docs".

        The restart takes a snapshot of each first, in .snapshots/ of the data folder.
        To restart without one: stack restart --no-snapshot
    """
    return (f"  The restart takes a snapshot of each first, in {store.SNAPSHOTS_FOLDER}/ "
            f"of the data folder.\n  {DIM}To restart without one: "
            f"stack restart --no-snapshot{RESET}")


# ── What the restart says ────────────────────────────────────────────────

def _refused_lines(cause: str, waiting: list[str], running: list[str]) -> list[str]:
    """What the restart says when it took no snapshot. It names the
    cause and the stacklets that are stopped and wait for their snapshot
    (`waiting`). It names those that still run the release they ran
    (`running`). It also names the two ways on.

        _refused_lines("the disk is full", ["docs"], [])
        #   ✗  No snapshot: the disk is full
        #      docs did not start on the new release, because …
        #      Free space or fix the cause, then: stack restart docs
        #      Or restart without a snapshot:    stack restart docs --no-snapshot
    """
    held = " ".join([*waiting, *running])
    states = []
    if waiting:
        states.append(f"{', '.join(waiting)} did not start on the new release")
    if running:
        states.append(f"{', '.join(running)} still runs the release it ran")
    return [f"\n  {RED}✗{RESET}  No snapshot: {cause}",
            f"     {'; '.join(states)}, because a migration of a release cannot be undone without one.",
            f"     Free space or fix the cause, then: stack restart {held}",
            f"     Or restart without a snapshot:    stack restart {held} --no-snapshot\n"]


def _unreadable_lines(error: pending.Unreadable) -> list[str]:
    """What the restart says when the kept record of a failed snapshot
    cannot be read: the file, and the two ways on.

        _unreadable_lines(Unreadable("docs", Path(".stack/snapshot-pending/docs/MANIFEST.json"), …))
        #   ✗  No snapshot: the record of the failed snapshot of docs cannot be read: …
        #      Restore the file, then:            stack restart docs
        #      Or restart without a snapshot:     stack restart docs --no-snapshot
    """
    return [f"\n  {RED}✗{RESET}  No snapshot: {error}",
            f"     {error.stacklet} waits for that snapshot and did not start.",
            f"     Restore the file from a backup, then: stack restart {error.stacklet}",
            f"     Or restart without a snapshot:        stack restart {error.stacklet} --no-snapshot\n"]


def _unknown_lines(ids: list[str]) -> list[str]:
    """What the restart says of the stacklets that run a commit nobody
    knows (`release.unknown_commits`): it takes no snapshot of them.

        _unknown_lines(["photos"])
        #   ⚠ No snapshot of photos: the commit it runs is unknown, so the
        #     stack cannot tell whether this restart applies a release.
    """
    return [f"  {ORANGE}⚠{RESET} No snapshot of {sid}: the commit it runs is unknown, so the\n"
            f"    stack cannot tell whether this restart applies a release." for sid in ids]


def _taken_lines(taken: Taken, pruned: release.Pruned) -> list[str]:
    """What the restart says after a snapshot: its name, whether it was
    copied, and each older snapshot it removed or could not remove.

        _taken_lines(taken, release.Pruned(removed=["2026-11-01T0900Z"]))
        #   ✓ Snapshot 2026-11-25T2114Z: docs
        #     Removed the snapshot 2026-11-01T0900Z; the newest 3 stay.
    """
    stacklets = ", ".join(taken.snapshot.manifest.stacklets) if taken.snapshot.manifest else ""
    lines = [f"  {GREEN}✓{RESET} Snapshot {taken.snapshot.name}: {stacklets}"]
    if not taken.cloned:
        lines.append(f"    {DIM}The data folder is not on APFS, so the files were copied.{RESET}")
    for name in pruned.removed:
        lines.append(f"    {DIM}Removed the snapshot {name}; the newest {release.KEEP} stay.{RESET}")
    for name, why in pruned.failed.items():
        lines.append(f"    {ORANGE}⚠{RESET} The snapshot {name} was not removed: {why}")
    return [*lines, ""]


# ── Ending a restart without a snapshot ─────────────────────────────────

def _exit(stck, ids: list[str], cause: str, lines: list[str]) -> NoReturn:
    """Record a restart without a snapshot as the result of each run of
    `ids`. Then print `lines` on stderr and exit 1."""
    for sid in ids:
        run_log.record_result(stck.instance_dir, sid,
                              {"error": f"No snapshot: {cause}", "stacklet": sid})
    print("\n".join(lines), file=sys.stderr)
    sys.exit(1)
