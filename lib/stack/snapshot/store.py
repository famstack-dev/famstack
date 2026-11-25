"""The snapshot store: the `.snapshots/` folder in the data folder.

A snapshot holds the state of stopped stacklets and the stack's
configuration, as they were before a change. This module keeps the
`.snapshots/` folder: it takes a snapshot, lists the snapshots, removes
one, and owns the names. It stops and starts nothing: the caller stops
the stacklets first (`stack snapshot create`, or the restart that
applies a release). `stack.snapshot.files` says what a snapshot holds
and how its files are cloned and cloned back.

    from stack.snapshot import store

    taken = store.take(data_root, instance_dir, {"docs": Path("stacklets/docs")},
                       manifest, running=stck.is_running)
    taken.snapshot.name        # "2026-11-24T2114Z"
    taken.cloned               # True on APFS, False when the files were copied
    store.snapshots(data_root) # [Snapshot(name="2026-11-24T2114Z", …), …], newest first
    store.remove(data_root, snapshot)   # renamed to `.removing-<name>`, then deleted
    store.discard(data_root, folder)    # the same for any entry of `.snapshots/`

One snapshot, on disk:

    ~/famstack-data/.snapshots/
    └── 2026-11-24T2114Z/
        ├── MANIFEST.json      the record: release, commit, images, migrations
        ├── stack/             stack.toml, users.toml, .stack/
        └── docs/              postgres/, paperless/data/, … (no paperless/media/)

Why it works this way:

- **The stacklets must be stopped.** A database server that writes while
  its folder is cloned leaves a torn database in the snapshot. `take`
  asks `running` for each stacklet first, and refuses before it writes
  anything when one runs.
- **A half snapshot is never listed.** `take` writes into
  `.snapshots/.taking-<name>/`, writes the manifest last, then renames
  the folder. `snapshots` lists only folders without a leading dot that
  hold a manifest. A take that fails removes its own `.taking-` folder.
- **The take only reads the data folder.** It writes only below
  `.snapshots/`.
- **Time Machine skips `.snapshots/`** (`tmutil addexclusion`, when the
  folder is made), because the snapshot is a clone of data that Time
  Machine already holds.
- **`.snapshots/` starts with a dot.** No part of the stack reads the
  folder as a stacklet's data folder, because a stacklet's id never
  starts with a dot.

The file system is behind the port `Volume` (`stack.snapshot.volume`).
The port lets a test stand in a volume that does not clone or is full.
The manifest's records come from `stack.manifest`. The caller makes them
while the stacklets still run, because a running container names the
image it runs.
"""

from __future__ import annotations

import contextlib
import re
import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .. import manifest as manifests
from ..manifest import Manifest, ManifestError
from . import files
from .errors import SnapshotError
from .volume import ThisVolume, Volume

__all__ = [
    "REMOVING",
    "REPLACED_FOLDER",
    "SNAPSHOTS_FOLDER",
    "TAKING",
    "Snapshot",
    "Taken",
    "discard",
    "is_entry_name",
    "is_name",
    "remove",
    "snapshots",
    "take",
]

SNAPSHOTS_FOLDER = ".snapshots"
"""The folder of the snapshots, in the data folder (`~/famstack-data/.snapshots/`)."""

REPLACED_FOLDER = "replaced"
"""The folder in `.snapshots/` that holds the state each rollback replaced."""

TAKING = ".taking-"
"""The prefix of a snapshot that is still being written. It starts with a
dot, so `snapshots` never lists it."""

REMOVING = ".removing-"
"""The prefix of a folder that is being removed, for the same reason."""

# A snapshot's name: the time it was taken, in UTC, to the minute. A second
# snapshot in the same minute gets "-2", "-3", ….
_NAME_TIME = "%Y-%m-%dT%H%MZ"
_NAME = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{4}Z)(?:-(\d+))?")


# ── The records ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Snapshot:
    """One snapshot in `.snapshots/`: its name, its folder and its manifest.

    `manifest` is None when the manifest cannot be read. `unreadable` then
    says why and names the file. A rollback never goes to such a snapshot.

        Snapshot(name="2026-11-24T2114Z",
                 path=Path("~/famstack-data/.snapshots/2026-11-24T2114Z"),
                 manifest=Manifest(kind="snapshot", version="0.4.0-rc.1", …))
        snapshot.taken   # datetime(2026, 11, 24, 21, 14, tzinfo=timezone.utc)
        Snapshot(name="2026-11-24T2100Z", path=…, manifest=None,
                 unreadable=".snapshots/2026-11-24T2100Z/MANIFEST.json: not a readable manifest: …")
    """

    name: str
    path: Path
    manifest: Manifest | None
    unreadable: str | None = None

    @property
    def taken(self) -> datetime:
        """When the snapshot was taken, in UTC, from its name."""
        return _time_of(self.name)


@dataclass(frozen=True)
class Taken:
    """What `take` made: the snapshot, and whether its files are clones
    (`cloned`) or copies.

        Taken(snapshot=Snapshot(name="2026-11-24T2114Z", …), cloned=True)
    """

    snapshot: Snapshot
    cloned: bool


# ── Taking and listing ───────────────────────────────────────────────────

def take(data_root: Path, instance_dir: Path, stacklets: dict[str, Path],
         manifest: Manifest, *, running: Callable[[str], bool],
         volume: Volume | None = None, now: datetime | None = None) -> Taken:
    """Take a snapshot of the stopped `stacklets` and of the configuration.

    `data_root` is the data folder (`~/famstack-data`), `instance_dir`
    holds `stack.toml`, `users.toml` and `.stack/`. `stacklets` maps each
    stacklet's id to its folder (`stacklets/docs`), where `take` reads its
    `[[backup.archive]]` sources. `manifest` is written as the snapshot's
    `MANIFEST.json`, with its `name` set to the snapshot's name. `running`
    says whether a stacklet still runs. `now` is the time of the snapshot,
    the current time by default.

    Refuses with `SnapshotError` when a stacklet still runs, and then
    writes nothing. Refuses with `SnapshotError` when the volume cannot
    clone and has too little free space for a copy, and then writes no
    snapshot. Raises `SnapshotError` when a file cannot be read or
    written. Any error, also Ctrl-C, removes the half snapshot. Only reads
    the data folders. Creates `.snapshots/` when it is missing, and tells Time
    Machine to skip it.

        take(Path("~/famstack-data"), Path("~/famstack"),
             {"docs": Path("stacklets/docs")}, manifest, running=stck.is_running)
        # Taken(snapshot=Snapshot(name="2026-11-24T2114Z", …), cloned=True)
    """
    data_root, volume = Path(data_root), volume or ThisVolume()
    still_running = [sid for sid in stacklets if running(sid)]
    if still_running:
        raise SnapshotError(f"{', '.join(still_running)} still runs: "
                            "stop it before its snapshot, because a running database "
                            "would be cloned half written")
    folder = _snapshots_folder(data_root, volume)
    parts = files.parts(data_root, Path(instance_dir), stacklets)
    cloned = volume.can_clone(folder)
    if not cloned:
        files.check_room(parts, folder, volume)
    name = _free_name(folder, now or datetime.now(timezone.utc))
    return Taken(_write(folder, name, parts, replace(manifest, name=name),
                        volume if cloned else None), cloned)


def snapshots(data_root: Path) -> list[Snapshot]:
    """The snapshots in `<data_root>/.snapshots/`, newest first.

    Lists each folder with a snapshot's name that holds a manifest. A
    folder without one is left out: a snapshot that is still being
    written or that stopped halfway, or the state a rollback moved aside.
    A manifest that cannot be read is listed with `unreadable` and no
    manifest. It is listed because one broken snapshot must not hide the
    others or stop an update that lists them. Empty when there is no
    `.snapshots/`. Changes nothing.

        snapshots(Path("~/famstack-data"))
        # [Snapshot(name="2026-11-24T2140Z", …), Snapshot(name="2026-11-24T2114Z", …)]
    """
    folder = Path(data_root) / SNAPSHOTS_FOLDER
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        if not (path.is_dir() and _NAME.fullmatch(path.name)):
            continue
        try:
            record = manifests.read(path)
        except ManifestError as error:
            found.append(Snapshot(path.name, path, None, unreadable=str(error)))
            continue
        if record is not None:
            found.append(Snapshot(path.name, path, record))
    return sorted(found, key=lambda s: _order_of(s.name), reverse=True)


def remove(data_root: Path, snapshot: Snapshot) -> None:
    """Remove `snapshot`, one that `snapshots(data_root)` listed, with its
    folder. Refuses and raises as `discard` does.

        remove(Path("~/famstack-data"), snapshots(Path("~/famstack-data"))[-1])
    """
    discard(data_root, snapshot.path)


def discard(data_root: Path, folder: Path) -> None:
    """Delete `folder`, one entry of `<data_root>/.snapshots/`, with
    everything in it. This is the one function of the package that
    deletes a folder: every remove goes through its checks.

    An entry is a folder directly in `.snapshots/` or in
    `.snapshots/replaced/`, with a snapshot's name (`is_name`) or that
    name after `.taking-` or `.removing-`. Refuses with `SnapshotError`,
    before anything changes:

    - a folder outside those two folders, also after `..` or a link;
    - `.snapshots/` itself, `replaced/` itself, and any other name;
    - a link: the entry, `.snapshots/` or `replaced/`, because a link
      leads out of `.snapshots/`;
    - a path that is not a folder.

    A folder with a name that does not start with a dot is renamed to
    `.removing-<name>` first and deleted after. Because of the rename, a
    removal that stops halfway leaves a folder that no list takes for
    whole. A folder whose name starts with a dot is not whole anyway,
    and is deleted where it is. Raises `SnapshotError` when the folder
    cannot be renamed, or when a file in it cannot be deleted: then it
    names the folder that stays.

        discard(Path("~/famstack-data"),
                Path("~/famstack-data/.snapshots/replaced/2026-11-25T2203Z"))
        discard(Path("~/famstack-data"), Path("~/famstack-data/.snapshots/replaced"))
        # SnapshotError: …/.snapshots/replaced: not an entry of .snapshots/, so it is not removed
    """
    folder = Path(folder)
    if not _is_entry(Path(data_root), folder):
        raise SnapshotError(f"{folder}: not an entry of {SNAPSHOTS_FOLDER}/, so it is not removed")
    if not folder.name.startswith("."):
        removing = folder.parent / f"{REMOVING}{folder.name}"
        try:
            folder.rename(removing)
        except OSError as error:
            raise SnapshotError(f"{folder} cannot be removed: {error}") from error
        folder = removing
    # Safe to delete, because the folder is in `.snapshots/` and is no
    # link. It holds clones, copies or a state a rollback moved aside.
    # rmtree deletes a link inside it, never what the link points at.
    shutil.rmtree(folder, ignore_errors=True)
    if folder.exists():
        left = next((str(p) for p in folder.rglob("*")), str(folder))
        raise SnapshotError(f"{folder} stays, because a file in it cannot be deleted: {left}. "
                            f"It is not whole any more.")


def is_name(name: str) -> bool:
    """Whether `name` has the form of a snapshot's name. A replaced
    state's folder has the same form.

        >>> is_name("2026-11-24T2114Z-2"), is_name("../docs")
        (True, False)
    """
    return _NAME.fullmatch(name) is not None


def is_entry_name(name: str) -> bool:
    """Whether `name` is the name of an entry of `.snapshots/`: a
    snapshot's name, or that name after `.taking-` or `.removing-`.

        >>> is_entry_name(".taking-2026-11-25T2300Z"), is_entry_name(".removing-2026-11-25T2300Z")
        (True, True)
        >>> is_entry_name("replaced"), is_entry_name(".taking-../docs")
        (False, False)
    """
    for prefix in (TAKING, REMOVING):
        if name.startswith(prefix):
            return is_name(name[len(prefix):])
    return is_name(name)


# ── Writing a snapshot ───────────────────────────────────────────────────

def _snapshots_folder(data_root: Path, volume: Volume) -> Path:
    """`.snapshots/` in `data_root`. When it is missing, it is made and
    excluded from Time Machine."""
    folder = data_root / SNAPSHOTS_FOLDER
    if not folder.is_dir():
        try:
            folder.mkdir(parents=True)
        except OSError as error:
            raise SnapshotError(f"{folder}: cannot be made: {error}") from error
        volume.exclude_from_backups(folder)
    return folder


def _write(folder: Path, name: str, parts: list[files.Part], record: Manifest,
           volume: Volume | None) -> Snapshot:
    """Write the snapshot `name` into `folder`: the parts into a `.taking-`
    folder, the manifest last, then the rename. Clones with `volume`, or
    copies when it is None. Any failure, also Ctrl-C, removes the
    `.taking-` folder. The manifest comes before the rename, because a
    folder with a snapshot's name must always hold its manifest."""
    partial, final = folder / f"{TAKING}{name}", folder / name
    renamed = False
    try:
        partial.mkdir()
        for part in parts:
            files.write_part(part, partial / part.target, volume)
        manifests.write(partial, record)
        partial.rename(final)
        renamed = True
    except (OSError, ManifestError) as error:
        raise SnapshotError(f"the snapshot {name} stopped halfway and was removed: {error}") from error
    finally:
        if not renamed:
            # This folder was made by this take, above, and holds only what
            # it wrote. A file that stays is listed as not whole.
            with contextlib.suppress(SnapshotError):
                discard(folder.parent, partial)
    return Snapshot(name, final, record)


def _free_name(folder: Path, now: datetime) -> str:
    """The name of a new snapshot taken at `now`: its time in UTC, with
    "-2", "-3", … when a snapshot of that minute exists."""
    base = now.astimezone(timezone.utc).strftime(_NAME_TIME)
    name, number = base, 1
    while (folder / name).exists() or (folder / f"{TAKING}{name}").exists():
        number += 1
        name = f"{base}-{number}"
    return name


# ── Names ────────────────────────────────────────────────────────────────

def _is_entry(data_root: Path, folder: Path) -> bool:
    """Whether `folder` is an entry of `<data_root>/.snapshots/` that
    `discard` may delete. Such an entry is a folder with an entry's name,
    and no link. Its parent is `.snapshots/` or `.snapshots/replaced/` of
    this data folder.

    The parent is compared after `resolve`, because `..` and a link
    above the data folder must lead to the same answer as the plain
    path. `.snapshots/` and `replaced/` must be no links themselves,
    because `resolve` follows them. A link there would make the parent
    compare equal wherever the link points.
    """
    snapshots = data_root / SNAPSHOTS_FOLDER
    replaced = snapshots / REPLACED_FOLDER
    parent = folder.parent.resolve()
    if parent not in (snapshots.resolve(), replaced.resolve()):
        return False
    if snapshots.is_symlink() or (parent == replaced.resolve() and replaced.is_symlink()):
        return False
    if folder.is_symlink() or not folder.is_dir():
        return False
    return is_entry_name(folder.name)


def _time_of(name: str) -> datetime:
    """The time in a snapshot's name.

        >>> _time_of("2026-11-24T2114Z-2")
        datetime.datetime(2026, 11, 24, 21, 14, tzinfo=datetime.timezone.utc)
    """
    match = _NAME.fullmatch(name)
    if match is None:
        raise ValueError(f"not a snapshot's name: {name}")
    return datetime.strptime(match.group(1), _NAME_TIME).replace(tzinfo=timezone.utc)


def _order_of(name: str) -> tuple[datetime, int]:
    """The sort key of a snapshot's name: its time, then its number."""
    match = _NAME.fullmatch(name)
    return _time_of(name), int(match.group(2) or 1) if match else 1


