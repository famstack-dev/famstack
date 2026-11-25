"""The record of a rollback: what it does, which steps are done, what it holds.

A rollback (`stack.snapshot.rollback`) writes its record before the first
state moves. The record goes into the folder that keeps the state the
rollback replaces. The rollback adds each step there once it is done:

    .snapshots/replaced/2026-11-25T2203Z/ROLLBACK.json
    {"snapshot": "2026-11-25T2114Z", "version": "0.4.0-rc.1",
     "stacklets": ["docs", "photos"], "starts": ["docs", "photos"],
     "archives": {"docs": ["paperless/media"], "photos": []},
     "done": ["moved docs", "restored docs", "moved photos"]}

    unfinished(data_root)          # [Path(".../replaced/2026-11-25T2203Z")]
    holding(data_root, "photos")   # that folder: photos must not start yet
    holding(data_root, "docs")     # that folder too, until "starting" is done
    why_held(folder, "docs")       # {"error": "…", "stacklet": "docs", "state": "whole"}
    needs(data_root, snapshot.path)  # that folder: the rollback needs the snapshot

Why it works this way:

- **The record names each step that is done.** After a stop halfway,
  the next `stack rollback` goes on from there. It never runs a done
  step a second time. The record is written beside the old one and
  renamed over it. Because of the rename, a stop while it is written
  leaves the old record or the new one.
- **A stacklet waits until the checkout is done.** Until the record
  names "starting", `holding` names it and `stack up` refuses its
  stacklets. Their state may be half restored, or the checkout may still
  be the new release's. That release's code must not start on the old
  state.

  The rollback writes "starting" only after git moved the checkout. A
  stop before that keeps the hold until the next `stack rollback`, also
  a stop inside the checkout. A release that does not know the record
  ignores the hold. It starts on whole states, because the checkout is
  the last step before the start. A finished record holds nothing.
- **A rollback that has not ended keeps its way back.** Because `needs`
  names it for its snapshot and for its replaced state, no remove and no
  prune deletes what the next `stack rollback` finishes from.
- **A record that cannot be read is named, never guessed.**
  `stack rollback` refuses and names its file. It holds each stacklet
  that has a folder beside it, because that state may be half moved. It
  holds no other stacklet.

A rollback passes three types between its plan and its steps: `Plan`,
`Done` and `RollbackError`. They live here too, because each of them
describes what the record holds.

Debt: a rollback can stop after the checkout, and a release without the
record can then start its stacklets. That rollback stays unfinished: it
names "starting" but not "finished". `needs` keeps its snapshot and its
replaced state. `stack snapshot remove` and the prune refuse them until
a release with `stack rollback` runs it once and finishes the record.
Trigger: an admin rolls back to a release without `stack rollback` and
then wants the disk space back.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..manifest import Manifest
from . import rollback_images as images
from . import store
from .errors import SnapshotError
from .store import REPLACED_FOLDER, Snapshot

__all__ = [
    "FINISHED",
    "MOVED",
    "RECORD_FILE",
    "REPLACED_FOLDER",
    "RESTORED",
    "RUN_ACTION",
    "STARTING",
    "Done",
    "Plan",
    "RollbackError",
    "begin",
    "holding",
    "mark",
    "needs",
    "read_record",
    "unfinished",
    "why_held",
]


RUN_ACTION = "rollback"
"""The action a rollback writes into the run locks it holds (`stack.runs`),
from its question until the checkout moved. A remove reads it
(`stack.snapshot.removal`)."""

RECORD_FILE = "ROLLBACK.json"
"""The record of one rollback, in its folder in `replaced/`."""

# The steps the record names when they are done. A step of one stacklet
# names it: "moved docs".
MOVED = "moved"
"""A stacklet's state is in the replaced folder."""
RESTORED = "restored"
"""The snapshot's state of a stacklet is back in its data folder."""
STARTING = "starting"
"""The checkout is on the snapshot's commit; only the start is left."""
FINISHED = "finished"
"""The rollback ended: its stacklets started."""

# The name of a replaced folder: the time the rollback began, in UTC.
_NAME_TIME = "%Y-%m-%dT%H%MZ"


# ── What a rollback does, and what it did ──────────────────────────────

class RollbackError(SnapshotError):
    """A rollback that refused, or a step that failed. The message says
    why and what to do. `problems` lists what it names (files, images).

        RollbackError("There is no snapshot to go back to. stack snapshot list shows them.")
    """

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = problems or []


@dataclass(frozen=True)
class Plan:
    """What a rollback does.

    - `snapshot`: the snapshot it goes back to.
    - `stacklets`: the stacklets it replaces, with their folders.
    - `starts`: the ones it starts, which are those that ran.
    - `archives`: their archive sources.
    - `newer`: the files in those sources that are newer than the
      snapshot.
    - `others`: the running stacklets outside the snapshot.
    - `tags`: the tags it moves.

    `replaced` and `done` are set when the plan finishes a rollback that
    stopped halfway.

        Plan(snapshot=Snapshot(name="2026-11-25T2114Z", …),
             stacklets={"docs": Path("stacklets/docs")}, starts=["docs"],
             archives={"docs": ["paperless/media"]},
             newer={"docs": {"paperless/media": 1}}, others=["photos"])
        plan.version     # "0.4.0-rc.1"
        plan.left        # ["restore docs", "start docs"]
    """

    snapshot: Snapshot
    stacklets: dict[str, Path]
    starts: list[str]
    archives: dict[str, list[str]]
    newer: dict[str, dict[str, int]] = field(default_factory=dict)
    others: list[str] = field(default_factory=list)
    tags: list[images.TagMove] = field(default_factory=list)
    replaced: Path | None = None
    done: tuple[str, ...] = ()

    @property
    def manifest(self) -> Manifest:
        """The snapshot's manifest. A plan is made only of a readable one."""
        assert self.snapshot.manifest is not None
        return self.snapshot.manifest

    @property
    def version(self) -> str:
        """The release the rollback goes back to, as the manifest names it."""
        return self.manifest.version

    @property
    def left(self) -> list[str]:
        """What the rollback still does: "restore <id>" for each stacklet
        whose state is not back yet, then "start <ids>"."""
        left = [f"restore {sid}" for sid in self.stacklets
                if f"{RESTORED} {sid}" not in self.done]
        return [*left, f"start {', '.join(self.starts)}"] if self.starts else left


@dataclass(frozen=True)
class Done:
    """What a rollback did. It names the snapshot and the release it went
    back to. It holds the folder of the state it replaced and the
    stacklets it started. It says whether the restored files are clones,
    and names the tags it moved.

        Done(snapshot="2026-11-25T2114Z", version="0.4.0-rc.1",
             replaced=Path("~/famstack-data/.snapshots/replaced/2026-11-25T2203Z"),
             started=["docs"], cloned=True, tags=[])
    """

    snapshot: str
    version: str
    replaced: Path
    started: list[str]
    cloned: bool
    tags: list[str] = field(default_factory=list)


# ── Which rollback holds a stacklet ────────────────────────────────────

def holding(data_root: Path, stacklet_id: str) -> Path | None:
    """The folder of a rollback that holds the stacklet. None when no
    rollback holds it.

    The stacklet's state may be moved aside or half restored, or the
    checkout may still be the new release's. For this reason the
    stacklet must not start until `stack rollback` has brought every
    state back and moved the checkout.

        holding(Path("~/famstack-data"), "docs")
        # Path(".../.snapshots/replaced/2026-11-25T2203Z") after a power loss in the restore
    """
    for folder in unfinished(data_root):
        try:
            record = read_record(folder)
        except RollbackError:
            if stacklet_id in _ids_beside(folder):
                return folder
            continue
        if stacklet_id in record["stacklets"] and STARTING not in record["done"]:
            return folder
    return None


def why_held(folder: Path, stacklet_id: str) -> dict:
    """The answer of a start that the rollback in `folder` holds. It
    contains the message, the stacklet and what is true of its state.
    `state` is "half restored" while the record does not name its
    restore. It is "whole" once the record does. When the state is
    whole, only the end of the rollback is left: the checkout, when it
    has not moved, and the start. `stack up` and every restart return
    this answer.

        why_held(folder, "photos")
        # {"error": "A rollback of photos stopped halfway, before its state was back. …",
        #  "stacklet": "photos", "state": "half restored"}

    A record that cannot be read gives its own message, and no `state`:
    the stack cannot tell how far the rollback got.
    """
    try:
        done = read_record(folder)["done"]
    except RollbackError as unreadable:
        return {"error": str(unreadable), "stacklet": stacklet_id}
    if f"{RESTORED} {stacklet_id}" not in done:
        return {"error": (f"A rollback of {stacklet_id} stopped halfway, before its state was "
                          f"back, so the state may be half restored ({folder}). Finish it with: "
                          f"stack rollback"),
                "stacklet": stacklet_id, "state": "half restored"}
    return {"error": (f"A rollback of {stacklet_id} stopped halfway. Its state is whole again, "
                      f"but the rollback has not ended ({folder}). Finish it with: stack "
                      f"rollback. It moves the checkout to the snapshot's release, unless it "
                      f"is there, and starts {stacklet_id}."),
            "stacklet": stacklet_id, "state": "whole"}


def needs(data_root: Path, folder: Path) -> Path | None:
    """The folder of a rollback that has not ended and needs `folder`: its
    snapshot (`.snapshots/<name>/`) or its own replaced folder. None when
    no rollback needs it, which means that it may go.

    A record that cannot be read needs every snapshot, because the stack
    cannot tell which one it goes back to.

        needs(Path("~/famstack-data"), Path(".../.snapshots/2026-11-25T2114Z"))
        # Path(".../.snapshots/replaced/2026-11-25T2203Z") after a stop halfway
    """
    folder = Path(folder)
    a_snapshot = folder.parent == Path(data_root) / store.SNAPSHOTS_FOLDER
    for rollback in unfinished(data_root):
        if folder == rollback:
            return rollback
        try:
            if a_snapshot and read_record(rollback)["snapshot"] == folder.name:
                return rollback
        except RollbackError:
            if a_snapshot:
                return rollback
    return None


def unfinished(data_root: Path) -> list[Path]:
    """The folders of the rollbacks that did not finish, oldest first: a
    record without "finished", or one that cannot be read.

        unfinished(Path("~/famstack-data"))   # [] once every rollback ended
    """
    parent = Path(data_root) / store.SNAPSHOTS_FOLDER / REPLACED_FOLDER
    if not parent.is_dir():
        return []
    folders = []
    for folder in sorted(parent.iterdir()):
        if not (folder / RECORD_FILE).is_file():
            continue
        try:
            if FINISHED in read_record(folder)["done"]:
                continue
        except RollbackError:
            pass
        folders.append(folder)
    return folders


def read_record(folder: Path) -> dict:
    """The record in `folder`. Raises `RollbackError` naming the file when
    it cannot be read, because a guess could move a state aside twice.

        read_record(folder)["done"]   # ["moved docs", "restored docs"]
    """
    path = folder / RECORD_FILE
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        for key in ("snapshot", "stacklets", "starts", "archives", "done"):
            record[key]
        return record
    except (OSError, ValueError, KeyError, TypeError) as error:
        # The admin decides, because the stack cannot tell how far the
        # rollback got. A rename is a way on that deletes nothing, because
        # a folder without a record holds nothing.
        raise RollbackError(f"{path}: the record of a rollback cannot be read: {error}. The "
                            f"stack cannot tell how far it got, and does not guess. The state "
                            f"it replaced is in {folder}. To keep the state the stacklets have "
                            f"now, rename the record: mv {path} {path}.unreadable",
                            [str(path)]) from error


# ── Writing the record ────────────────────────────────────────────────

def begin(data_root: Path, planned: Plan, now: datetime) -> Path:
    """Make the replaced folder of a new rollback, with a name from `now`.
    Write its record with no step done. Returns the folder. Raises
    `RollbackError` when it cannot. In that case no state has moved.

        begin(data_root, planned, now)   # Path(".../replaced/2026-11-25T2203Z")
    """
    parent = data_root / store.SNAPSHOTS_FOLDER / REPLACED_FOLDER
    base = now.astimezone(timezone.utc).strftime(_NAME_TIME)
    folder, number = parent / base, 1
    while folder.exists():
        number += 1
        folder = parent / f"{base}-{number}"
    try:
        folder.mkdir(parents=True)
        _write_record(folder, {"snapshot": planned.snapshot.name, "version": planned.version,
                               "stacklets": list(planned.stacklets),
                               "starts": list(planned.starts),
                               "archives": planned.archives, "done": []})
    except OSError as error:
        raise RollbackError(f"{folder}: the record of the rollback cannot be written: "
                            f"{error}. Nothing was replaced.") from error
    return folder


def mark(folder: Path, done: list[str], step: str) -> None:
    """Add `step` to the steps that are done, in `done` and in the record
    in `folder`. Does nothing when it is there.

        mark(folder, done, "moved docs")
    """
    if step in done:
        return
    done.append(step)
    _write_record(folder, {**read_record(folder), "done": list(done)})


def _write_record(folder: Path, record: dict) -> None:
    """Write the record beside the old one and rename it over it.
    Because of the rename, a stop halfway leaves the old record or the
    new one."""
    partial = folder / f".{RECORD_FILE}.partial"
    with open(partial, "w", encoding="utf-8") as out:
        out.write(json.dumps(record, indent=2) + "\n")
        out.flush()
        os.fsync(out.fileno())
    os.replace(partial, folder / RECORD_FILE)


def _ids_beside(folder: Path) -> set[str]:
    """The stacklets that have an entry in a replaced folder: `docs`, `docs.2`."""
    return {entry.name.split(".")[0] for entry in folder.iterdir() if entry.is_dir()}
