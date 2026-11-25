"""The guards that keep a start or a snapshot from losing the way back.

A release is applied with a snapshot first only by `stack restart`
(`stack.snapshot.restart_cli`). Two other commands could lose that way
back, and ask here before they act:

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

`starting` refuses those too. It also refuses a running stacklet on a
different release than the checkout's (`stack.snapshot.release.applying`).
That is an older release after `stack update`, or a newer one after a
rollback of other stacklets. `stack up` would recreate it on the
checkout's code and run its migrations with no snapshot.

A stopped stacklet is not refused, because nothing runs that a restart
would record. The restart that applies a release stops its stacklets
before it starts them. For this reason the guard never refuses it.

Every refusal is an answer `{"error": ..., "stacklet": ..., "refused":
True}` that names the command that goes on. The guards change nothing,
and nothing failed. A caller that reports a failed start (the Server
room's notice) leaves a refusal out by its `refused` key.

Debt: the guard fails open. Three things leave a stacklet out of
`release.applying`:

- A `docker ps` that times out or fails.
- A container without the `stack.commit` label.
- A commit that git cannot diff (one the checkout no longer has).

In each case, `stack up` applies a release without a snapshot and says
nothing.
Trigger: the first report of a release applied
without a snapshot, or the `unknown` list of `stack restart` growing past
the containers of old installs.

Debt: any commit under `lib/` makes every running stacklet stale. For
that reason, `stack up <id>` after a change of `stack.toml` is refused
on a development checkout until `stack restart <id>`. Trigger: a second
complaint from a developer or a bot that edits the configuration. Then
`stack up` could take the snapshot itself.
"""

from __future__ import annotations

from .. import runs
from . import pending, release, rollback

__all__ = ["refusals", "split", "starting", "waiting"]


def starting(stck, stacklet_id: str) -> dict | None:
    """The refusal of `stack up <stacklet_id>`, or None when it may
    start: `waiting`, then a running stacklet on another release.

        starting(stck, "docs")   # after `stack update`, docs running:
        # {"error": "docs runs a different release ... stack restart docs ...", ...}
    """
    return waiting(stck, stacklet_id) or _applying(stck, stacklet_id)


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
    about, because that run may be the restart that applies the release.
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
                                 f"last restart. Take it with: stack restart {stacklet_id}. Or "
                                 f"start without one: stack restart {stacklet_id} --no-snapshot")


def _applying(stck, stacklet_id: str) -> dict | None:
    """The refusal of a start that would apply a release with no
    snapshot. The stacklet runs a commit whose files differ from the
    checkout's. That commit is older, or newer after a rollback of other
    stacklets. None in three cases: the stacklet is stopped, its commit
    is unknown or it runs the checkout's code."""
    if stacklet_id not in release.applying(stck, [stacklet_id]):
        return None
    return _refused(stacklet_id, f"{stacklet_id} runs a different release than the checkout. "
                                 f"A start would apply the checkout's release without a snapshot. "
                                 f"Apply it with: stack restart {stacklet_id}, which takes a "
                                 f"snapshot first. Or without one: stack restart {stacklet_id} "
                                 f"--no-snapshot")


def _refused(stacklet_id: str, error: str) -> dict:
    """The answer of a refusal: the error, the stacklet, and `refused`."""
    return {"error": error, "stacklet": stacklet_id, "refused": True}
