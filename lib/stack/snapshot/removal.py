"""What `stack snapshot remove` deletes: a snapshot, a replaced state, a half folder.

`.snapshots/` holds three kinds of entries. The admin removes each one by
its name, as `stack snapshot list` prints it:

    ~/famstack-data/.snapshots/
    ├── 2026-11-25T2114Z/            snapshot       name: 2026-11-25T2114Z
    ├── .taking-2026-11-25T2300Z/    incomplete     name: .taking-2026-11-25T2300Z
    └── replaced/
        └── 2026-11-25T2203Z/        replaced       name: replaced/2026-11-25T2203Z

    from stack.snapshot import removal

    entry = removal.plan(data_root, instance_dir, "2026-11-25T2114Z")   # changes nothing
    entry.kind, entry.version, entry.stacklets, entry.size_bytes
    # ("snapshot", "0.4.0-rc.1", ["docs", "photos"], 1_240_000)
    removal.remove(data_root, entry)                                    # deletes it
    removal.replaced(data_root)      # [Entry(name="replaced/2026-11-25T2203Z", …)]
    removal.incomplete(data_root)    # [Entry(name=".taking-2026-11-25T2300Z", …)]

A **replaced state** is the state that a rollback moved aside
(`stack.snapshot.rollback_record`). An **incomplete** folder is what a
stop halfway left: a take (`.taking-`) or a removal (`.removing-`). No
list takes it for a snapshot, and nothing else deletes it.

Why it works this way:

- **Only names, never paths.** `plan` takes a name of one of the forms
  above, and refuses every other one: `..`, an absolute path, a
  stacklet's folder, a link. The family's data is outside `.snapshots/`,
  and the remove never reaches it.
- **The way back of an open rollback stays.** A rollback that has not
  ended finishes from its snapshot and its replaced state
  (`rollback_record.needs`), so `plan` refuses both and names `stack
  rollback`.
- **Nothing whole goes while a rollback goes on.** A rollback holds the
  run locks of its stacklets from its question until their state is
  restored, as the action `rollback` (`stack.runs`). Its lock does not
  say which snapshot it restores, so `plan` refuses every snapshot and
  replaced state meanwhile. After the restore its record keeps them
  (`rollback_record.needs`) until the rollback ends. A remove that came
  before the lock is caught by the rollback: it checks its snapshot
  again before it stops anything.
- **A remove holds the locks of the entry's stacklets** from its plan to
  its delete, and plans again under them (`stack.snapshot.remove_cli`).
  A rollback that starts during the remove's question is refused, and a
  rollback that came between the two plans is seen by the second one.
- **A half folder waits while a run goes on,** because a take may be
  writing it in that moment (`stack.runs`).
- **A snapshot with an unreadable manifest can go.** The admin named it,
  and no rollback goes to it anyway.
- **The size is the sum of the file sizes.** On APFS a clone shares its
  blocks with the live file until one of them changes, so the space that
  comes free can be less.

The command line (`stack.snapshot.remove_cli`) shows the entry, asks for the
confirmation, and calls `remove`. The store (`stack.snapshot.store`)
does the delete.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from .. import runs
from ..manifest import ManifestError
from ..manifest import read as read_manifest
from . import store
from .errors import SnapshotError
from .rollback_record import RUN_ACTION, RollbackError, needs, read_record
from .store import REPLACED_FOLDER

__all__ = [
    "INCOMPLETE",
    "REPLACED",
    "SNAPSHOT",
    "Entry",
    "RemovalError",
    "incomplete",
    "plan",
    "remove",
    "replaced",
]

SNAPSHOT = "snapshot"
"""The kind of a whole snapshot."""
REPLACED = "replaced"
"""The kind of a state a rollback moved aside."""
INCOMPLETE = "incomplete"
"""The kind of a folder a stop halfway left."""


class RemovalError(SnapshotError):
    """A remove that the stack refused. The message says why and what to do.

        RemovalError("docs is not a snapshot or a replaced state. stack snapshot list shows them.")
    """


@dataclass(frozen=True)
class Entry:
    """One entry of `.snapshots/` that the admin can remove.

    `name` is the name the admin types. `version` and `stacklets` come
    from the manifest or the rollback's record; `unreadable` says why
    they are missing. `snapshot` and `finished` are set for a replaced
    state. `size_bytes` is set by `plan` only, because the lists do not
    need it.

        Entry(name="replaced/2026-11-25T2203Z", kind="replaced",
              path=Path(".../.snapshots/replaced/2026-11-25T2203Z"),
              version="0.4.0-rc.1", stacklets=["docs", "photos"],
              snapshot="2026-11-25T2114Z", finished=True)
    """

    name: str
    kind: str
    path: Path
    version: str | None = None
    stacklets: list[str] = field(default_factory=list)
    unreadable: str | None = None
    snapshot: str | None = None
    finished: bool | None = None
    size_bytes: int | None = None


# ── Planning and removing ────────────────────────────────────────────────

def plan(data_root: Path, instance_dir: Path, name: str) -> Entry:
    """The entry `name` names, with its size, after every refusal.

    Raises `RemovalError` when `name` is not the name of a snapshot, a
    replaced state or an incomplete folder in `<data_root>/.snapshots/`;
    when a rollback that has not ended needs it; when it is a snapshot
    or a replaced state and a rollback goes on or waits at its question;
    or when it is an incomplete folder and a run of the stack goes on.
    Changes nothing.

        plan(Path("~/famstack-data"), Path("~/famstack"), "2026-11-25T2114Z")
        # Entry(name="2026-11-25T2114Z", kind="snapshot", size_bytes=1_240_000, …)
        plan(Path("~/famstack-data"), Path("~/famstack"), "../docs")
        # RemovalError: ../docs is not a snapshot or a replaced state …
    """
    data_root = Path(data_root)
    entry = _found(data_root, name)
    if entry.kind == INCOMPLETE:
        _refuse_while_a_run_goes_on(Path(instance_dir), entry)
    else:
        _refuse_while_a_rollback_goes_on(Path(instance_dir), entry)
        _refuse_the_way_back(data_root, entry)
    return replace(entry, size_bytes=_size_of(entry.path))


def remove(data_root: Path, entry: Entry) -> None:
    """Delete the folder of `entry`, one that `plan(data_root, …)` returned.

    Refuses with `SnapshotError` a path that is not an entry of
    `<data_root>/.snapshots/` (`store.discard`). Raises it when the folder
    cannot be renamed, or when a file in it cannot be deleted; then it
    names the folder that stays, and `stack snapshot list` shows that
    folder as incomplete.

        remove(data_root, plan(data_root, instance_dir, "replaced/2026-11-25T2203Z"))
    """
    store.discard(data_root, entry.path)


def replaced(data_root: Path) -> list[Entry]:
    """The states that rollbacks moved aside, oldest first, without sizes.

    A folder whose record cannot be read, or that has none (the admin
    renamed it), is listed with `unreadable`. Changes nothing.

        replaced(Path("~/famstack-data"))
        # [Entry(name="replaced/2026-11-25T2203Z", kind="replaced", finished=True, …)]
    """
    parent = Path(data_root) / store.SNAPSHOTS_FOLDER / REPLACED_FOLDER
    if not parent.is_dir():
        return []
    return [_replaced(path) for path in sorted(parent.iterdir())
            if store.is_name(path.name) and path.is_dir() and not path.is_symlink()]


def incomplete(data_root: Path) -> list[Entry]:
    """The folders that a stop halfway left, by name, without sizes: a
    take (`.taking-`) and a removal (`.removing-`), also one of a replaced
    state. Changes nothing.

        incomplete(Path("~/famstack-data"))
        # [Entry(name=".taking-2026-11-25T2300Z", kind="incomplete", …)]
    """
    folder = Path(data_root) / store.SNAPSHOTS_FOLDER
    found = []
    for parent, prefix in ((folder, ""), (folder / REPLACED_FOLDER, f"{REPLACED_FOLDER}/")):
        if not parent.is_dir():
            continue
        found += [Entry(f"{prefix}{path.name}", INCOMPLETE, path)
                  for path in sorted(parent.iterdir())
                  if _is_half(path.name) and path.is_dir() and not path.is_symlink()]
    return found


# ── Finding the entry of a name ──────────────────────────────────────────

def _found(data_root: Path, name: str) -> Entry:
    """The entry `name` names; refused when there is none."""
    folder = data_root / store.SNAPSHOTS_FOLDER
    head, _, tail = name.partition("/")
    if head == REPLACED_FOLDER and (store.is_name(tail) or _is_half(tail)):
        path = folder / REPLACED_FOLDER / tail
    elif not tail and (store.is_name(head) or _is_half(head)):
        path = folder / head
    else:
        raise RemovalError(f"{name or '(empty)'} is not a snapshot or a replaced state. "
                           f"stack snapshot list shows them.")
    if path.is_symlink() or not path.is_dir() or path.parent.is_symlink():
        raise RemovalError(f"There is no {name}. stack snapshot list shows the snapshots.")
    if _is_half(path.name):
        return Entry(name, INCOMPLETE, path)
    if path.parent.name == REPLACED_FOLDER:
        return _replaced(path)
    return _snapshot(name, path)


def _snapshot(name: str, path: Path) -> Entry:
    """A snapshot's entry; refused when its folder holds no manifest,
    because then it is no snapshot of the store."""
    try:
        record = read_manifest(path)
    except ManifestError as error:
        return Entry(name, SNAPSHOT, path, unreadable=str(error))
    if record is None:
        raise RemovalError(f"{name} holds no manifest, so it is not a snapshot of the stack. "
                           f"stack snapshot list shows the snapshots.")
    return Entry(name, SNAPSHOT, path, version=record.version,
                 stacklets=list(record.stacklets))


def _replaced(path: Path) -> Entry:
    """A replaced state's entry, from the rollback's record."""
    name = f"{REPLACED_FOLDER}/{path.name}"
    try:
        record = read_record(path)
    except RollbackError as error:
        return Entry(name, REPLACED, path, unreadable=str(error))
    return Entry(name, REPLACED, path, version=record.get("version"),
                 stacklets=list(record["stacklets"]), snapshot=record["snapshot"],
                 finished="finished" in record["done"])


def _is_half(name: str) -> bool:
    """Whether `name` is the name of a folder a stop halfway left.

        >>> _is_half(".taking-2026-11-25T2300Z"), _is_half(".removing-2026-11-25T2300Z")
        (True, True)
        >>> _is_half(".taking-../docs")
        False
    """
    return name.startswith((store.TAKING, store.REMOVING)) and store.is_entry_name(name)


# ── The refusals ─────────────────────────────────────────────────────────

def _refuse_the_way_back(data_root: Path, entry: Entry) -> None:
    """Refuse the snapshot or the replaced state of a rollback that has
    not ended: the next `stack rollback` finishes from both."""
    rollback = needs(data_root, entry.path)
    if rollback is None:
        return
    try:
        read_record(rollback)
    except RollbackError as unreadable:
        raise RemovalError(f"{entry.name} is kept, because a rollback has not ended and its "
                           f"record cannot be read, so the stack cannot tell what it needs: "
                           f"{unreadable}") from unreadable
    raise RemovalError(f"{entry.name} is kept, because the rollback in {rollback} has not "
                       f"ended and needs it. Finish it first: stack rollback")


def _refuse_while_a_rollback_goes_on(instance_dir: Path, entry: Entry) -> None:
    """Refuse a snapshot or a replaced state while a rollback holds the
    run locks of its stacklets: it may be about to restore that snapshot."""
    holders = [holder for _stacklet, holder in runs.held_elsewhere(instance_dir)]
    if (holder := runs.running(instance_dir)) is not None:
        holders.append(holder)
    rolling = next((h for h in holders if h.get("action") == RUN_ACTION), None)
    if rolling is None:
        return
    raise RemovalError(f"{entry.name} is kept while a rollback goes on "
                       f"({runs.describe(rolling)}), because it may restore it. "
                       f"Try again when the rollback ended.")


def _refuse_while_a_run_goes_on(instance_dir: Path, entry: Entry) -> None:
    """Refuse an incomplete folder while a run of the stack goes on,
    because a take may be writing it."""
    holder = runs.running(instance_dir)
    others = runs.held_elsewhere(instance_dir)
    if holder is None and not others:
        return
    what = runs.describe(holder) if holder is not None else runs.describe(others[0][1])
    raise RemovalError(f"{entry.name} is kept while a run goes on ({what}), because a "
                       f"snapshot may be writing it. Try again when the run ended.")


# ── Sizes ────────────────────────────────────────────────────────────────

def _size_of(folder: Path) -> int:
    """The sum of the sizes of the files below `folder`, links not followed."""
    total = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            path = Path(root) / name
            if not path.is_symlink():
                total += path.stat().st_size
    return total
