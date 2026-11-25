"""The rollback: back to the snapshot before the last update, code, images and state.

A release with a one-way migration can break a page. The restart that
applied it took a snapshot of each stacklet it moved
(`stack.snapshot.release`). The rollback goes back to that snapshot:

    from stack.snapshot import rollback

    planned = rollback.plan(stck)              # the newest snapshot; changes nothing
    planned.snapshot.name                      # "2026-11-25T2114Z"
    planned.stacklets                          # {"docs": Path("stacklets/docs"), …}
    planned.newer                              # ["docs/paperless/media/new-bill.pdf"]
    done = rollback.run(stck, planned, stop=cli.down_many, start=start_in_a_process)
    done.replaced                              # .snapshots/replaced/2026-11-25T2203Z

On disk, after a rollback of docs:

    ~/famstack-data/
    ├── docs/                       the snapshot's state, and paperless/media/ as it was
    └── .snapshots/
        ├── 2026-11-25T2114Z/       the snapshot, unchanged
        └── replaced/
            └── 2026-11-25T2203Z/
                ├── ROLLBACK.json   the record of this rollback and its steps
                └── docs/           the state the rollback replaced, whole

The rollback is four modules, one topic each:

    rollback          the plan: which snapshot, and the refusals before any change
    rollback_steps    the steps: stop, move, restore, tag, check out, start
    rollback_record   the record of each rollback, and which stacklets it holds
    rollback_images   the images: the check before the stop, the tags before the start

Why the plan works this way:

- **It refuses before any change.** A missing or unreadable snapshot
  ends the command with nothing stopped. A stacklet that the checkout
  does not have ends it too. An image that the Mac no longer holds and
  local edits in the checkout end it as well.
- **The newest snapshot, or the one named.** A snapshot whose manifest
  cannot be read is refused. An older one that can be read is not taken
  instead, because it undoes more than the last update.
- **A rollback that stopped halfway is finished first.** Its plan comes
  from its record (`rollback_record`), and a rollback to another snapshot
  is refused until it is.
- **The archive sources stay where they are.** The snapshot left out the
  `[[backup.archive]]` sources (the photo library, the document
  originals). The rollback never touches them. The files that came
  in after the snapshot stay, and the restored database does not know
  them. The plan names each one by its path under the data folder,
  newest first. The admin needs the paths to find the files.
- **Local edits in the checkout are refused, not set aside.** The admin
  decides what to do with them (commit, stash), because the rollback
  ends on another commit where the edits may not apply.
- **The configuration is not restored.** The snapshot's `stack/` holds
  it. A restore would lose a password, a paired phone or a person added
  since. A release also reads the configuration of the next one.

Debt: after "starting", the finish checks the images and the tags no
more. The order is right, because the tags moved before the record named
"starting". But a stop in the start leaves a window until the next
`stack rollback`.

In that window, the admin guide sends the admin
through `stack update` and back. Watchtower or a stacklet outside the
rollback that shares a tag can move the tag. An image prune can remove
the image. The finish then starts on another image. Trigger: the first
report of a finish on another image, or a pull that keeps a local tag.

Debt: the plan reads the `[[backup.archive]]` sources from the checkout
before it moves, not from the snapshot. The two are the same today,
because each restart that applies a release takes its own snapshot.
Trigger: the manifest records the sources, or a checkout moves without a
restart.
"""

from __future__ import annotations

from pathlib import Path

from ..images import DockerImages, ImageSource
from ..manifest import MANIFEST_FILE, Manifest
from ..updater import Checkout
from . import files, store
from . import rollback_images as images
from .errors import SnapshotError
from .rollback_record import (
    RECORD_FILE,
    REPLACED_FOLDER,
    STARTING,
    Done,
    Plan,
    RollbackError,
    elsewhere,
    holding,
    read_record,
    unfinished,
    why_held,
)
from .rollback_steps import run
from .store import Snapshot

__all__ = [
    "RECORD_FILE",
    "REPLACED_FOLDER",
    "Done",
    "Plan",
    "RollbackError",
    "holding",
    "plan",
    "read_record",
    "run",
    "why_held",
]


def plan(stck, name: str | None = None, source: ImageSource | None = None) -> Plan:
    """The plan of `stack rollback [<name>]` on the instance `stck`.

    Goes back to the snapshot `name`, or the newest one. A rollback that
    stopped halfway is finished first: its plan is returned, with
    `replaced` and `done` set. `source` asks Docker for images. Changes
    nothing. Raises `RollbackError` in these cases:

    - There is no snapshot.
    - The snapshot or a record cannot be read.
    - The snapshot names a stacklet that this checkout does not have.
    - The Mac no longer holds an image of a stacklet that it starts.
    - The checkout has local edits.

        plan(stck)                         # Plan(snapshot=Snapshot(name="2026-11-25T2114Z", …), …)
        plan(stck, "2026-11-01T0900Z")     # that snapshot
    """
    source = source or DockerImages()
    stopped = unfinished(stck.data)
    if stopped:
        return _resumed(stck, stopped[0], name, source)
    snapshot = _chosen(store.snapshots(stck.data), name)
    manifest = snapshot.manifest
    assert manifest is not None  # _chosen refuses an unreadable one
    stacklets = _known(stck, snapshot, list(manifest.stacklets))
    starts = [sid for sid, record in manifest.stacklets.items() if record.up]
    archives = {sid: _archives(path, stck.data) for sid, path in stacklets.items()}
    _check_images(snapshot.name, manifest, starts, source)
    _check_checkout(stck, manifest.commit)
    others = _others(stck, stacklets)
    return Plan(snapshot, stacklets, starts, archives,
                newer=_newer(stck.data, snapshot, archives), others=others,
                tags=images.moves(manifest, starts, others, source))


# ── Choosing the snapshot ────────────────────────────────────────────────

def _chosen(snapshots: list[Snapshot], name: str | None) -> Snapshot:
    """The snapshot `name`, or the newest one. Refused when there is none
    or it cannot be read."""
    if not snapshots:
        raise RollbackError("There is no snapshot to go back to. "
                            "`stack snapshot list` shows the snapshots.")
    if name is None:
        snapshot = snapshots[0]
    else:
        snapshot = next((s for s in snapshots if s.name == name), None)
        if snapshot is None:
            raise RollbackError(f"There is no snapshot {name}. "
                                f"`stack snapshot list` shows the snapshots.")
    if snapshot.manifest is None:
        # Never an older one on its own: it undoes more than the last update.
        raise RollbackError(f"The snapshot {snapshot.name} cannot be read, so the stack cannot "
                            f"go back to it: {snapshot.unreadable}. To go back further, name "
                            f"another snapshot: stack rollback <name> (stack snapshot list).")
    return snapshot


def _resumed(stck, folder: Path, name: str | None, source: ImageSource) -> Plan:
    """The plan that finishes the rollback recorded in `folder`."""
    record = read_record(folder)
    if name not in (None, record["snapshot"]):
        raise RollbackError(f"The rollback of {folder.name} to {record['snapshot']} stopped "
                            f"halfway. Finish it first: stack rollback")
    done = tuple(record["done"])
    if STARTING in done and (moved_on := elsewhere(stck.root, folder, record)):
        # The checkout moved on after the stop in the start, for example
        # with `stack update`. Its migrations would run without a snapshot.
        raise RollbackError(moved_on)
    snapshot = next((s for s in store.snapshots(stck.data) if s.name == record["snapshot"]), None)
    if snapshot is None or snapshot.manifest is None:
        raise RollbackError(f"The rollback of {folder.name} to {record['snapshot']} stopped "
                            f"halfway, and that snapshot is gone or cannot be read. The state "
                            f"it replaced is in {folder}.")
    stacklets = _known(stck, snapshot, record["stacklets"])
    if STARTING not in done:
        _check_images(snapshot.name, snapshot.manifest, record["starts"], source)
        _check_checkout(stck, snapshot.manifest.commit)
    others = _others(stck, stacklets)
    return Plan(snapshot, stacklets, list(record["starts"]), record["archives"],
                newer=_newer(stck.data, snapshot, record["archives"]), others=others,
                tags=images.moves(snapshot.manifest, record["starts"], others, source),
                replaced=folder, done=done)


def _known(stck, snapshot: Snapshot, ids: list[str]) -> dict[str, Path]:
    """The folder of each stacklet among `ids`. Refused when the checkout
    does not have one."""
    known = {s["id"]: Path(s["path"]) for s in stck.discover()}
    missing = [sid for sid in ids if sid not in known]
    if missing:
        raise RollbackError(f"The snapshot {snapshot.name} holds {', '.join(missing)}, "
                            f"which this checkout does not have.", missing)
    return {sid: known[sid] for sid in ids}


def _archives(stacklet_dir: Path, data_root: Path) -> list[str]:
    """The stacklet's archive sources, relative to its data folder."""
    try:
        return [str(path) for path in files.archive_paths(stacklet_dir, data_root)]
    except SnapshotError as error:
        raise RollbackError(str(error)) from error


# ── The checks before any change ─────────────────────────────────────────

def _check_images(name: str, manifest: Manifest, starts: list[str],
                  source: ImageSource) -> None:
    """Refuse when the Mac no longer holds an image of a stacklet that
    starts, naming each such image and the snapshot `name`."""
    missing = images.missing(manifest, starts, source)
    if missing:
        raise RollbackError(f"The Mac no longer holds the images that the snapshot {name} "
                            f"started: {'; '.join(missing)}. Without them the stacklets would "
                            f"start on other images. Nothing was changed.", missing)


def _check_checkout(stck, commit: str) -> None:
    """Refuse a checkout that is not git or that does not know `commit`.
    Refuse it also when it has local edits and is on another commit."""
    checkout = Checkout(stck.root)
    if not checkout.is_git():
        raise RollbackError("This is not a git checkout, so the stack cannot go back to the "
                            "release of the snapshot.")
    target = checkout.commit_of(commit)
    if not target:
        raise RollbackError(f"git does not know the commit {commit} of the snapshot. "
                            f"Fetch it first: git fetch --tags")
    if checkout.commit_of("HEAD") == target:
        return
    dirty = checkout.dirty_paths()
    if dirty:
        raise RollbackError(f"The checkout has local edits: {', '.join(dirty)}. The rollback "
                            f"checks out the commit of the snapshot. Commit the edits or set "
                            f"them aside (git stash), then run: stack rollback", dirty)


def _newer(data_root: Path, snapshot: Snapshot, archives: dict[str, list[str]]) -> list[str]:
    """The files in the archive sources that are newer than the snapshot,
    each by its path under the data folder, newest first:
    `["docs/paperless/media/new-bill.pdf"]`. Newest first, because the
    admin looks for what came in last, and a large import would otherwise
    fill the shown part. The snapshot's time is its manifest's, which the
    take writes last."""
    taken = (Path(snapshot.path) / MANIFEST_FILE).stat().st_mtime
    late = [(path.stat().st_mtime, str(path.relative_to(data_root)))
            for sid, sources in archives.items() for source in sources
            for path in (data_root / sid / source).rglob("*")
            if path.is_file() and path.stat().st_mtime > taken]
    return [path for _, path in sorted(late, key=lambda item: (-item[0], item[1]))]


def _others(stck, stacklets: dict[str, Path]) -> list[str]:
    """The running stacklets that the rollback does not replace."""
    return sorted(stck.running_ids() - set(stacklets))
