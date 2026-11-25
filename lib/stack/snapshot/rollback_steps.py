"""The steps of a rollback, and the record that lets the next run finish one.

`stack.snapshot.rollback` makes the plan. This module runs it. The
steps, in order:

    stop       the stacklets of the snapshot; one that still runs ends it
    record     .snapshots/replaced/<time>/ROLLBACK.json: what this rollback does
    move       each one's state into replaced/<time>/<id>/; the archive sources stay
    restore    clone the snapshot's state back (`files.clone_back`)
    clear      the record of a failed take (`stack.snapshot.pending`)
    tag        each moved tag names the manifest's image again (`rollback_images`)
    checkout   the snapshot's commit
    start      the stacklets that ran, by the old release's own `stack up`

    done = run(stck, planned, stop=cli.down_many, start=start_in_a_process)
    done.replaced          # .snapshots/replaced/2026-11-25T2203Z

Why it works this way:

- **The checkout is the last step before the start.** Until every state
  is back, the new release's code stays on disk. A rollback can stop
  there (a power loss, a file it cannot read). Then that release's
  `stack rollback` finishes it, and its `stack up` refuses the stacklets
  meanwhile.

  After the checkout every state is whole. A release that knows the
  record still holds the stacklets until its `stack rollback` writes
  "starting". A release that has no `stack rollback` does not know the
  record. Its own `stack up` starts them safely.
- **The snapshot is checked again under the run locks,** before the
  stop. A remove may have deleted it while the admin read the plan. In
  that case the run ends before anything stops. From there on, a remove
  refuses every snapshot while the rollback holds its locks
  (`stack.snapshot.removal`).
- **Nothing of the family's moves before the record.** A stop that fails
  ends the run with each state where it was and no record. A record that
  cannot be written (a full disk) ends it the same way. The stacklets
  that it stopped start again on the code they ran, because the checkout
  has not moved yet.
- **The record names each step that is done.** A stop halfway leaves it,
  and the next `stack rollback` goes on from there. It writes "starting"
  only after git moved the checkout. The record holds the stacklets until
  then (`stack.snapshot.rollback_record`).

  Git can refuse the checkout (a lock, an untracked file in the way). A
  Ctrl-C or a power loss can stop it. Each of these leaves the record as
  it was. Nothing starts, and the next `stack rollback` goes on with the
  checkout.
- **It never deletes a state, and never writes over one.** A state moves
  aside by a rename on the same volume. A move that finds the entry in
  the replaced folder already refuses. A restore first moves what is in
  the data folder now into a new entry (`<id>.2`, `<id>.3`). Because of
  this, a restore that runs a second time replaces nothing. The files of
  one that stopped halfway replace nothing either, because the clone
  refuses to write over a file.
- **The old release starts its own stacklets.** After the checkout, the
  hooks and the framework on disk are the old release's. For this reason
  the start is a `stack up` process of its own. It runs after the run
  locks are released.

Debt: after the checkout this process may import a module of the old
release. Every module of the run imports at its top, and nothing checks
it. Trigger: an import inside a function on the path of `run`.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .. import runs
from ..images import DockerImages, ImageSource
from ..updater import Checkout
from . import files, pending, store
from . import rollback_images as images
from .running import anything_up
from .rollback_record import (
    FINISHED,
    MOVED,
    RESTORED,
    STARTING,
    Done,
    Plan,
    RollbackError,
    begin,
    mark,
)
from .volume import Volume

__all__ = ["run"]


# ── Running ──────────────────────────────────────────────────────────────

def run(stck, planned: Plan, *, stop: Callable[[list[str]], dict],
        start: Callable[[list[str]], bool], volume: Volume | None = None,
        now: datetime | None = None, source: ImageSource | None = None) -> Done:
    """Run `planned`: stop, record, move aside, restore, clear, tag, check
    out, start. Finishes a plan that stopped halfway from its record.

    - `stop(ids)` stops stacklets (`Lifecycle.down`).
    - `start(ids)` starts them and says whether they started.
    - `volume` is the file system of the data folder.
    - `now` names the replaced folder of a new rollback.
    - `source` asks Docker for images.

    Holds the run locks of the stacklets until the checkout moved, and
    lets them go before the start. Raises `RollbackError` when a step
    fails. Before the record, each stacklet it stopped is started again.
    After the record, nothing starts. The record keeps what is done for
    the next `stack rollback`.

        run(stck, plan(stck), stop=cli.down_many, start=start_in_a_process)
        # Done(snapshot="2026-11-25T2114Z", version="0.4.0-rc.1", …)
    """
    source = source or DockerImages()
    if STARTING in planned.done:
        folder, done, cloned, moved = planned.replaced, list(planned.done), True, []
    else:
        folder, done, cloned, moved = _replace(stck, planned, stop, start, volume, now, source)
    assert folder is not None
    if not start(planned.starts):
        raise RollbackError(f"The state of the snapshot is back, but not every stacklet "
                            f"started: {', '.join(planned.starts)}. Find the cause in "
                            f"`stack logs <id>`, then run `stack rollback` again: it only "
                            f"starts them.")
    mark(folder, done, FINISHED)
    return Done(planned.snapshot.name, planned.version, folder, list(planned.starts),
                cloned, moved)


# ── Stop, replace, check out ─────────────────────────────────────────────

class _Undo(RollbackError):
    """A refusal before the record: nothing of the family's moved, and
    `stopped` names the stacklets to start again."""

    def __init__(self, message: str, stopped: list[str], problems: list[str] | None = None):
        super().__init__(message, problems)
        self.stopped = stopped


def _replace(stck, planned: Plan, stop, start, volume, now, source):
    """The steps under the run locks, up to "starting". Returns the
    folder and the steps done. Also returns whether the files are clones
    and the tags moved. A refusal before the record starts again what it
    stopped."""
    ids = list(planned.stacklets)
    with runs.hold(stck.instance_dir, ids):
        try:
            folder = _stopped_and_recorded(stck, planned, stop, now)
        except _Undo as undo:
            refused = undo
        else:
            done = list(planned.done)
            cloned = _replace_states(stck, planned, folder, done, volume)
            pending.clear(stck.instance_dir, ids)
            moved = _retag(planned, source)
            _check_out(stck, planned.manifest.commit)
            mark(folder, done, STARTING)
            return folder, done, cloned, moved
    if planned.replaced is None and refused.stopped and not start(refused.stopped):
        raise RollbackError(f"{refused} {', '.join(refused.stopped)} did not start again: "
                            f"stack up {' '.join(refused.stopped)}", refused.problems)
    raise RollbackError(str(refused), refused.problems)


def _stopped_and_recorded(stck, planned: Plan, stop, now: datetime | None) -> Path:
    """Check the snapshot again, stop, and make the record of a new
    rollback. Returns the replaced folder. Raises `_Undo` before the
    record, with the stacklets it stopped."""
    _refuse_a_snapshot_that_went(stck, planned)
    running = _stop(stck, list(planned.stacklets), stop)
    if planned.replaced is not None:
        return planned.replaced
    try:
        return begin(stck.data, planned, now or datetime.now(timezone.utc))
    except RollbackError as error:
        raise _Undo(str(error), running) from error


def _refuse_a_snapshot_that_went(stck, planned: Plan) -> None:
    """Raise `_Undo` when the planned snapshot is no longer whole in the
    store. A cause is a `stack snapshot remove` that deleted it while
    the admin read the plan, before this run held the locks. Under the
    locks a remove refuses it. For this reason the snapshot that this
    finds stays until the end."""
    name = planned.snapshot.name
    if any(s.name == name and s.manifest is not None for s in store.snapshots(stck.data)):
        return
    raise _Undo(f"The snapshot {name} is gone since the plan. Nothing was stopped or moved. "
                f"stack snapshot list shows the snapshots.", [])


def _stop(stck, ids: list[str], stop: Callable[[list[str]], dict]) -> list[str]:
    """Stop the stacklets among `ids` that have anything up, and return
    them. Raises `_Undo` when one still runs: a running database is
    never moved."""
    up = anything_up(stck)
    running = [sid for sid in ids if up(sid)]
    if running:
        stop(running)
    up = anything_up(stck)
    still = [sid for sid in ids if up(sid)]
    if still:
        raise _Undo(f"{', '.join(still)} did not stop, and a running database is never "
                    f"moved. Stop it with `stack down {' '.join(still)}`, then run "
                    f"`stack rollback` again.",
                    [sid for sid in running if sid not in still], still)
    return running


def _replace_states(stck, planned: Plan, folder: Path, done: list[str],
                    volume: Volume | None) -> bool:
    """Move each stacklet's state aside and clone the snapshot's back,
    skipping what the record names as done. Returns whether every
    restored file is a clone."""
    cloned = True
    for sid in planned.stacklets:
        target, archives = stck.data / sid, planned.archives.get(sid, [])
        if f"{MOVED} {sid}" not in done:
            _move_aside(target, folder / sid, archives)
            mark(folder, done, f"{MOVED} {sid}")
        if f"{RESTORED} {sid}" not in done:
            # A restore never writes over a file: what is there now (the
            # files of a restore that stopped) moves aside first.
            if any(_movable(target, {Path(a) for a in archives})):
                _move_aside(target, _new_entry(folder, sid), archives)
            snapshot = planned.snapshot
            cloned = files.clone_back(snapshot.path, snapshot.name, sid, target, volume) and cloned
            mark(folder, done, f"{RESTORED} {sid}")
    return cloned


def _retag(planned: Plan, source: ImageSource) -> list[str]:
    """Point each moved tag at the manifest's image. Raise on a refusal."""
    moved, failed = images.retag(planned.manifest, planned.starts, source)
    if failed:
        raise RollbackError(f"Docker refused to tag an image again: {'; '.join(failed)}. "
                            f"Run `stack rollback` again once the cause is fixed.", failed)
    return moved


def _check_out(stck, commit: str) -> None:
    """Move the checkout to `commit` after every state is back, unless it
    is there. Raises `RollbackError` when git refuses. The record is left
    as it is. It holds the stacklets until "starting" is written after the
    move."""
    checkout = Checkout(stck.root)
    if checkout.commit_of("HEAD") == checkout.commit_of(commit):
        return
    moved, error = checkout.checkout(commit)
    if not moved:
        raise RollbackError(f"Every state is back, but the checkout cannot move to {commit}: "
                            f"{error}. Nothing was started. Run `stack rollback` again once "
                            f"the cause is fixed: it checks out and starts.")


# ── Moving a state aside ─────────────────────────────────────────────────

def _move_aside(source: Path, target: Path, archives: list[str]) -> None:
    """Move each entry of the data folder `source` into `target` by
    rename. The archive sources stay. Goes on with what is left after a
    stop halfway. Never replaces an entry in `target`."""
    for relative in list(_movable(source, {Path(a) for a in archives})):
        moved = target / relative
        if moved.exists() or moved.is_symlink():
            raise RollbackError(f"{moved} is there already, so {source / relative} was not "
                                f"moved over it. The rollback stopped.", [str(moved)])
        try:
            moved.parent.mkdir(parents=True, exist_ok=True)
            os.rename(source / relative, moved)
        except OSError as error:
            raise RollbackError(f"{source / relative} cannot be moved aside: {error}. "
                                f"Run `stack rollback` again once the cause is fixed.") from error
    target.mkdir(parents=True, exist_ok=True)


def _movable(root: Path, archives: set[Path], relative: Path = Path()):
    """The entries below `root` to move aside: each one that is neither an
    archive source nor a folder that holds one. Such a folder is entered."""
    folder = root / relative
    if not folder.is_dir() or folder.is_symlink():
        return
    for name in sorted(os.listdir(folder)):
        entry = relative / name
        if entry in archives:
            continue
        path = root / entry
        if path.is_dir() and not path.is_symlink() and any(a.is_relative_to(entry) for a in archives):
            yield from _movable(root, archives, entry)
        else:
            yield entry


def _new_entry(folder: Path, stacklet_id: str) -> Path:
    """The next free entry for a second state of the stacklet: `<id>.2`, `<id>.3`, …"""
    number = 2
    while (folder / f"{stacklet_id}.{number}").exists():
        number += 1
    return folder / f"{stacklet_id}.{number}"
