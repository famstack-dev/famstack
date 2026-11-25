"""The snapshot of a restart, as `stack restart` and `stack up` call it.

The restart that applies a release (`stack restart` in `stack.cli`) takes
its snapshot in three calls around its own stop and start. They print
what the restart says and exit 1 when no snapshot was taken; the
decisions are in `stack.snapshot.release`. A fourth call, `waiting`,
refuses the start of a stacklet whose last snapshot failed, or whose
rollback stopped halfway:

    from stack.snapshot import restart_cli

    before = restart_cli.record_or_exit(stck, ["docs"], no_snapshot=False)   # while docs runs
    cli.down_many(["docs"])
    taken = restart_cli.take_or_exit(stck, before)        # nothing starts without it
    cli.up_many(["docs"])
    restart_cli.keep_and_say(stck, taken)                 # the newest 3 stay
    restart_cli.waiting(stck, "docs")                     # None, or the refusal
"""

from __future__ import annotations

import sys

from .. import run_log
from . import pending, release, rollback
from .release import Before
from .store import SnapshotError, Taken

__all__ = ["keep_and_say", "record_or_exit", "take_or_exit", "waiting"]


def record_or_exit(stck, ids: list[str], no_snapshot: bool) -> Before:
    """`release.before_stop` for a restart of `ids`, before it stops them.
    `no_snapshot` is `stack restart --no-snapshot`. Exits 1 with the cause
    and the two ways on when the record fails; then nothing has stopped.
    Names each stacklet whose commit is unknown, which gets no snapshot.

        record_or_exit(stck, ["docs"], no_snapshot=False)   # Before(stacklets={"docs": …}, …)
    """
    try:
        before = release.before_stop(stck, ids, skip=no_snapshot)
    except pending.Unreadable as error:
        _exit(stck, [error.stacklet], str(error), release.unreadable_lines(error))
    except SnapshotError as error:
        _exit(stck, ids, str(error), release.refused_lines(str(error), [], ids))
    if before.unknown:
        print("\n".join(release.unknown_lines(before.unknown)), file=sys.stderr)
    return before


def take_or_exit(stck, before: Before) -> Taken | None:
    """`release.after_stop` between the stop and the start of the
    stacklets `before` recorded: the snapshot, or None when `before` is
    empty. Exits 1 with the cause and the two ways on when the take
    fails; then they stay stopped and wait for their snapshot
    (`waiting`).

        take_or_exit(stck, before).snapshot.name   # "2026-11-25T2114Z"
    """
    stacklets = list(before.stacklets)
    try:
        return release.after_stop(stck, before)
    except release.StillRunning as error:
        waits = [sid for sid in stacklets if sid not in error.ids]
        _exit(stck, stacklets, str(error), release.refused_lines(str(error), waits, error.ids))
    except SnapshotError as error:
        _exit(stck, stacklets, str(error), release.refused_lines(str(error), stacklets, []))


def waiting(stck, stacklet_id: str) -> dict | None:
    """The answer of a start refused because the stacklet's last snapshot
    failed (`stack.snapshot.pending`), with the two ways on, or because a
    rollback of it stopped halfway (`stack.snapshot.rollback`). None when
    the stacklet waits for nothing. `stack up` and every restart ask it
    before they start a stacklet.

        waiting(stck, "docs")
        # {"error": "docs waits for its snapshot: … stack restart docs …"}
    """
    held = rollback.holding(stck.data, stacklet_id)
    if held is not None:
        try:
            rollback.read_record(held)
        except rollback.RollbackError as unreadable:
            return {"error": str(unreadable), "stacklet": stacklet_id}
        return {"error": (f"A rollback of {stacklet_id} stopped halfway, so its state may be "
                          f"half restored ({held}). Finish it with: stack rollback"),
                "stacklet": stacklet_id}
    if not pending.waiting(stck.instance_dir, stacklet_id):
        return None
    return {"error": (f"{stacklet_id} waits for its snapshot, which failed at the last restart. "
                      f"Take it with: stack restart {stacklet_id}. Or start without one: "
                      f"stack restart {stacklet_id} --no-snapshot"),
            "stacklet": stacklet_id}


def keep_and_say(stck, taken: Taken | None) -> None:
    """After a restart that took the snapshot `taken`: keep the newest
    snapshots (`release.prune`) and print what was taken and removed.
    Says nothing without a snapshot.

        keep_and_say(stck, taken)
        #   ✓ Snapshot 2026-11-25T2114Z: docs
        #     Removed the snapshot 2026-11-01T0900Z; the newest 3 stay.
    """
    if taken is not None:
        print("\n".join(release.taken_lines(taken, release.prune(stck.data, taken.snapshot.name))))


def _exit(stck, ids: list[str], cause: str, lines: list[str]):
    """Record a restart without a snapshot as the result of each run of
    `ids`, print `lines` on stderr, and exit 1."""
    for sid in ids:
        run_log.record_result(stck.instance_dir, sid,
                              {"error": f"No snapshot: {cause}", "stacklet": sid})
    print("\n".join(lines), file=sys.stderr)
    sys.exit(1)
