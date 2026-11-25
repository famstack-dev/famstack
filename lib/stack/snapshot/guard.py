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
(`stack.snapshot.pending`) or whose rollback stopped halfway
(`stack.snapshot.rollback`): a start would run on a state that has no
snapshot or is half restored, and a new snapshot of that state would
become the newest one, where the next rollback goes. `starting` refuses
those too, and also a running stacklet on a different release than the
checkout's (`stack.snapshot.release.applying`). That is an older release
after `stack update`, or a newer one after a rollback of other stacklets.
`stack up` would recreate it on the checkout's code and run its
migrations with no snapshot. A stopped
stacklet is not refused, because nothing runs that a restart would
record; the restart that applies a release stops its stacklets before
it starts them, so the guard never refuses it.

Every refusal is an answer `{"error": ..., "stacklet": ..., "refused":
True}` that names the command that goes on. The guards change nothing,
and nothing failed: a caller that reports a failed start (the Server
room's notice) leaves a refusal out by its `refused` key.

Debt (epic review): the guard fails open. `docker ps` that times out or
fails, a container without the `stack.commit` label, and a commit git
cannot diff (one the checkout no longer has) all leave a stacklet out of
`release.applying`, and `stack up` then applies a release without a
snapshot and says nothing. Trigger: the first report of a release applied
without a snapshot, or the `unknown` list of `stack restart` growing past
the containers of old installs.

Debt (epic review): any commit under `lib/` makes every running stacklet
stale, so on a development checkout `stack up <id>` after a change of
`stack.toml` is refused until `stack restart <id>`. Trigger: a second
complaint from a developer or a bot that edits the configuration; then
`stack up` could take the snapshot itself.
"""

from __future__ import annotations

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
    with its refusal; empty when none. `stack up` asks it before its setup
    questions and preparation, `stack ai key new` before it makes a key.

        refusals(stck, ["docs", "photos"])   # {"docs": {"error": "...", "refused": True}}
    """
    return {sid: refusal for sid in stacklet_ids if (refusal := starting(stck, sid))}


def split(stck, stacklet_ids: list[str]) -> tuple[dict[str, dict], list[str]]:
    """`refusals` of `stacklet_ids`, and the stacklets among them that may
    start, in their order. `stack up` starts the second and names the first.

        split(stck, ["docs", "photos"])   # ({"docs": {...}}, ["photos"])
    """
    refused = refusals(stck, stacklet_ids)
    return refused, [sid for sid in stacklet_ids if sid not in refused]


def waiting(stck, stacklet_id: str) -> dict | None:
    """The refusal of a start or a snapshot of a stacklet whose last
    snapshot failed, with the two ways on, or whose rollback stopped
    halfway. None when the stacklet waits for nothing.

        waiting(stck, "docs")
        # {"error": "docs waits for its snapshot: … stack restart docs …", "stacklet": "docs"}
    """
    held = rollback.holding(stck.data, stacklet_id)
    if held is not None:
        return {**rollback.why_held(held, stacklet_id), "refused": True}
    if not pending.waiting(stck.instance_dir, stacklet_id):
        return None
    return _refused(stacklet_id, f"{stacklet_id} waits for its snapshot, which failed at the "
                                 f"last restart. Take it with: stack restart {stacklet_id}. Or "
                                 f"start without one: stack restart {stacklet_id} --no-snapshot")


def _applying(stck, stacklet_id: str) -> dict | None:
    """The refusal of a start that would apply a release with no
    snapshot: the stacklet runs a commit whose files differ from the
    checkout's, older or, after a rollback of other stacklets, newer.
    None when it is stopped, its commit is unknown, or it runs the
    checkout's code."""
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
