"""The guards that keep a start or a snapshot from losing the way back.

A snapshot is taken only by `stack restart --with-snapshot`
(`stack.snapshot.restart_cli`). A failed take and a rollback that
stopped halfway leave a stacklet waiting. Two other commands could lose
the way back of such a stacklet, and ask here before they act:

    from stack.snapshot import guard

    guard.starting(stck, "docs")   # `stack up docs`: None, or the refusal
    guard.refusals(stck, ["docs", "photos"])   # `stack up docs photos`: each refusal
    guard.split(stck, ["docs", "photos"])      # the refusals, and the stacklets that may start
    guard.waiting(stck, "docs")    # `stack snapshot create docs`: None, or the refusal

`waiting` refuses a stacklet whose last snapshot failed
(`stack.snapshot.pending`). It also refuses a stacklet whose rollback
stopped halfway (`stack.snapshot.rollback`). A start would run on a
state that has no snapshot or is half restored. A new snapshot of that
state would become the newest one, where the next rollback goes.

`starting` refuses the same for `stack up`. A running stacklet on
another release than the checkout's is not refused: the snapshot is
opt-in, so `stack up` starts it on the checkout's code, as a restart
without `--with-snapshot` does.

Every refusal is an answer `{"error": ..., "stacklet": ..., "refused":
True}` that names the command that goes on. The guards change nothing,
and nothing failed. A caller that reports a failed start (the Server
room's notice) leaves a refusal out by its `refused` key.
"""

from __future__ import annotations

from .. import runs
from . import pending, rollback

__all__ = ["refusals", "split", "starting", "waiting"]


def starting(stck, stacklet_id: str) -> dict | None:
    """The refusal of `stack up <stacklet_id>`, or None when it may
    start: the refusal of `waiting`.

        starting(stck, "docs")   # after a failed snapshot of docs:
        # {"error": "docs waits for its snapshot ... stack restart docs ...", ...}
    """
    return waiting(stck, stacklet_id)


def refusals(stck, stacklet_ids: list[str]) -> dict[str, dict]:
    """Each stacklet among `stacklet_ids` whose start `starting` refuses,
    with its refusal. Empty when none. `stack up` asks it before its setup
    questions and preparation, `stack ai key new` before it makes a key.

        refusals(stck, ["docs", "photos"])   # {"docs": {"error": "...", "refused": True}}
    """
    return {sid: refusal for sid in stacklet_ids if (refusal := starting(stck, sid))}


def split(stck, stacklet_ids: list[str]) -> tuple[dict[str, dict], list[str]]:
    """`refusals` of `stacklet_ids`, and the stacklets among them that may
    start, in their order. `stack up` starts the second and names the first.

    Because `stack up` asks it before it claims anything, a run lock
    that is held belongs to another run. Such a stacklet is not asked
    about, because that run may be the restart that takes its snapshot.
    The run lock refuses the start with its own answer (exit 75).

        split(stck, ["docs", "photos"])   # ({"docs": {...}}, ["photos"])
    """
    free = [sid for sid in stacklet_ids if not runs.is_held(stck.instance_dir, sid)]
    refused = refusals(stck, free)
    return refused, [sid for sid in stacklet_ids if sid not in refused]


def waiting(stck, stacklet_id: str) -> dict | None:
    """The refusal of a start or a snapshot of a waiting stacklet. The
    stacklet's last snapshot failed, or its rollback stopped halfway. The
    refusal of a failed snapshot names the two ways on. None when the
    stacklet waits for nothing.

        waiting(stck, "docs")
        # {"error": "docs waits for its snapshot: … stack restart docs …", "stacklet": "docs"}
    """
    held = rollback.holding(stck.data, stacklet_id, stck.root)
    if held is not None:
        return {**rollback.why_held(held, stacklet_id, stck.root), "refused": True}
    if not pending.waiting(stck.instance_dir, stacklet_id):
        return None
    return _refused(stacklet_id, f"{stacklet_id} waits for its snapshot, which failed at the "
                                 f"last restart. Take it with: stack restart {stacklet_id} "
                                 f"--with-snapshot. Or start without one: "
                                 f"stack restart {stacklet_id}")


def _refused(stacklet_id: str, error: str) -> dict:
    """The answer of a refusal: the error, the stacklet, and `refused`."""
    return {"error": error, "stacklet": stacklet_id, "refused": True}
