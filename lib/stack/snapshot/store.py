"""The snapshot store: the `.snapshots/` folder in the data folder.

A snapshot holds the state of stopped stacklets and the stack's
configuration, as they were before a change. This module takes a
snapshot, lists the snapshots, removes one and clones one back. It stops and starts nothing: the caller
stops the stacklets first (`stack.snapshot.cli`, later the restart that
applies a release).

    from stack.snapshot import store

    taken = store.take(data_root, instance_dir, {"docs": Path("stacklets/docs")},
                       manifest, running=stck.is_running)
    taken.snapshot.name        # "2026-11-24T2114Z"
    taken.cloned               # True on APFS, False when the files were copied
    store.snapshots(data_root) # [Snapshot(name="2026-11-24T2114Z", …), …], newest first
    store.remove(snapshot)     # renamed to `.removing-<name>`, then deleted
    store.clone_back(snapshot, "docs", data_root / "docs")   # the rollback's restore

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
- **A file is a clone when the volume can clone.** On APFS a clone
  (`clonefile(2)`, what `cp -c` uses) takes no space until one of the
  two files changes, and a change of the live file never changes the
  snapshot. On another file system the files are copied. A copy needs
  the space, so `take` refuses when the disk has too little free space,
  and names the size.
- **The large files are left out.** Each stacklet declares them as
  `[[backup.archive]]` sources in its `stacklet.toml` (the photo
  library, the document originals). A rollback never replaces them, and a
  copy of them could fill the disk.
- **A half snapshot is never listed.** `take` writes into
  `.snapshots/.taking-<name>/`, writes the manifest last, then renames
  the folder. `snapshots` lists only folders without a leading dot that
  hold a manifest. A take that fails removes its own `.taking-` folder.
- **The take only reads the data folder.** It writes only below
  `.snapshots/`.
- **Time Machine skips `.snapshots/`** (`tmutil addexclusion`, when the
  folder is made), because the snapshot is a clone of data that Time
  Machine already holds.
- **`.snapshots/` starts with a dot.** A stacklet's id never does, so no
  part of the stack reads the folder as a stacklet's data folder.

The file system is behind the port `Volume` (`ThisVolume` is the real
one), so a test can stand in a volume that does not clone or is full. The
manifest's records come from `stack.manifest`; the caller makes them while
the stacklets still run, because a running container names the image it
runs.
"""

from __future__ import annotations

import ctypes
import errno
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from .. import manifest as manifests
from ..manifest import Manifest, ManifestError

__all__ = [
    "CONFIG_FOLDER",
    "SNAPSHOTS_FOLDER",
    "Snapshot",
    "SnapshotError",
    "Taken",
    "ThisVolume",
    "Volume",
    "archive_paths",
    "clone_back",
    "remove",
    "snapshots",
    "take",
]

SNAPSHOTS_FOLDER = ".snapshots"
"""The folder of the snapshots, in the data folder (`~/famstack-data/.snapshots/`)."""

CONFIG_FOLDER = "stack"
"""The folder in a snapshot that holds the stack's configuration."""

# What the configuration folder holds, from the instance folder. A file
# that the instance does not have is left out.
_CONFIG_ITEMS = ("stack.toml", "users.toml", ".stack")

# The prefix of a snapshot that is still being written. It starts with a
# dot, so `snapshots` never lists it.
_TAKING = ".taking-"

# The prefix of a snapshot that is being removed, for the same reason.
_REMOVING = ".removing-"

# A snapshot's name: the time it was taken, in UTC, to the minute. A second
# snapshot in the same minute gets "-2", "-3", ….
_NAME_TIME = "%Y-%m-%dT%H%MZ"
_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{4}Z)(?:-(\d+))?$")


# ── The records ──────────────────────────────────────────────────────────

class SnapshotError(Exception):
    """A snapshot that the store refused or could not take. The message
    says why, and names the stacklet or the size.

        SnapshotError("docs still runs: stop it before its snapshot")
    """


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


class Volume(Protocol):
    """The file system of the data folder, as the store uses it.

    `ThisVolume` is the real one. A test stands in a volume that does not
    clone, that is full, or whose clone fails:

        class CopyOnly(ThisVolume):            # a disk that is not APFS
            def can_clone(self, folder): return False
            def free_bytes(self, folder): return 100

        take(…, volume=CopyOnly())             # SnapshotError: "… needs 1240 bytes and the disk has 100 bytes free"
    """

    def can_clone(self, folder: Path) -> bool:
        """Whether a file in `folder` can be cloned. May write and remove a
        probe file in `folder`."""
        ...

    def clone(self, source: Path, target: Path) -> None:
        """Clone the file `source` as `target`, with its mode and times.
        Raises `OSError` when it cannot."""
        ...

    def free_bytes(self, folder: Path) -> int:
        """The free space on the volume of `folder`, in bytes."""
        ...

    def exclude_from_backups(self, folder: Path) -> None:
        """Tell Time Machine to skip `folder`. Never raises."""
        ...


class ThisVolume:
    """`Volume` over the file system of this Mac.

        volume = ThisVolume()
        volume.can_clone(Path("~/famstack-data/.snapshots"))   # True on APFS
        volume.clone(Path("a"), Path("b"))                     # b is a clone of a

    A clone is `clonefile(2)`, the call that `cp -c` makes. On a system
    without it (Linux) or a volume that does not support it, `can_clone`
    is False and the store copies. `exclude_from_backups` runs
    `tmutil addexclusion`; without `tmutil` it does nothing.
    """

    def can_clone(self, folder: Path) -> bool:
        """Whether `clonefile(2)` works in `folder`: clones a probe file
        there and removes both files again."""
        if _clonefile() is None:
            return False
        probe, copy = Path(folder) / ".clone-probe", Path(folder) / ".clone-probe-copy"
        try:
            probe.write_bytes(b"")
            self.clone(probe, copy)
            return True
        except OSError:
            return False
        finally:
            probe.unlink(missing_ok=True)
            copy.unlink(missing_ok=True)

    def clone(self, source: Path, target: Path) -> None:
        """Clone `source` as `target` with `clonefile(2)`. Raises `OSError`
        with the system's reason when the call fails."""
        clonefile = _clonefile()
        if clonefile is None:
            raise OSError(f"{source}: this system cannot clone files")
        if clonefile(os.fsencode(source), os.fsencode(target), ctypes.c_uint32(0)) != 0:
            errno = ctypes.get_errno()
            raise OSError(errno, os.strerror(errno), str(source))

    def free_bytes(self, folder: Path) -> int:
        """The free space on the volume of `folder`, in bytes."""
        return shutil.disk_usage(folder).free

    def exclude_from_backups(self, folder: Path) -> None:
        """`tmutil addexclusion <folder>`. A failure is ignored: the
        snapshot is whole without it, and Time Machine then only holds
        the clone a second time."""
        tmutil = shutil.which("tmutil")
        if tmutil is None:
            return
        try:
            subprocess.run([tmutil, "addexclusion", str(folder)],
                           capture_output=True, timeout=_TMUTIL_SECONDS, check=False)
        except (OSError, subprocess.SubprocessError):
            pass


# How long `tmutil addexclusion` may take. It only sets an attribute.
_TMUTIL_SECONDS = 30


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
    parts = _parts(data_root, Path(instance_dir), stacklets)
    cloned = volume.can_clone(folder)
    if not cloned:
        _check_room(parts, folder, volume)
    name = _free_name(folder, now or datetime.now(timezone.utc))
    return Taken(_write(folder, name, parts, replace(manifest, name=name),
                        volume if cloned else None), cloned)


def snapshots(data_root: Path) -> list[Snapshot]:
    """The snapshots in `<data_root>/.snapshots/`, newest first.

    Lists each folder with a snapshot's name that holds a manifest. A
    folder without one is left out: a snapshot that is still being
    written or that stopped halfway, or the state a rollback moved aside.
    A manifest that cannot be read is listed with `unreadable` and no
    manifest, because one broken snapshot must not hide the others or
    stop an update that lists them. Empty when there is no `.snapshots/`.
    Changes nothing.

        snapshots(Path("~/famstack-data"))
        # [Snapshot(name="2026-11-24T2140Z", …), Snapshot(name="2026-11-24T2114Z", …)]
    """
    folder = Path(data_root) / SNAPSHOTS_FOLDER
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        if not (path.is_dir() and _NAME.match(path.name)):
            continue
        try:
            record = manifests.read(path)
        except ManifestError as error:
            found.append(Snapshot(path.name, path, None, unreadable=str(error)))
            continue
        if record is not None:
            found.append(Snapshot(path.name, path, record))
    return sorted(found, key=lambda s: _order_of(s.name), reverse=True)


def remove(snapshot: Snapshot) -> None:
    """Remove `snapshot`, one that `snapshots` listed, with its folder.

    The folder is renamed to `.removing-<name>` first and deleted after,
    so a removal that stops halfway leaves a folder that `snapshots`
    never lists, and never half a snapshot under its name. Refuses with
    `SnapshotError` a path that is not a snapshot's folder in
    `.snapshots/`, and raises it when the folder cannot be renamed.

        remove(snapshots(Path("~/famstack-data"))[-1])
    """
    path = Path(snapshot.path)
    if path.parent.name != SNAPSHOTS_FOLDER or not _NAME.match(path.name) or not path.is_dir():
        raise SnapshotError(f"{path}: not a snapshot in {SNAPSHOTS_FOLDER}/, so it is not removed")
    removing = path.parent / f"{_REMOVING}{path.name}"
    try:
        path.rename(removing)
    except OSError as error:
        raise SnapshotError(f"the snapshot {path.name} cannot be removed: {error}") from error
    # Safe to delete: the folder is a snapshot of the store, renamed above,
    # and holds clones or copies. The data folders are never touched.
    shutil.rmtree(removing, ignore_errors=True)


def clone_back(snapshot: Snapshot, stacklet_id: str, target: Path,
               volume: Volume | None = None) -> bool:
    """Clone the state of `stacklet_id` in `snapshot` into `target`, the
    stacklet's data folder, and return whether the files are clones.

    `target` holds only what the snapshot left out: the rollback moved
    the rest aside before. Each entry of the snapshot is cloned into
    `target`. A folder that is there already (the parent of an
    `[[backup.archive]]` source) is entered, and its other entries stay. A
    file or a link that is there already is never replaced: the call
    refuses. The snapshot is only read. Raises `SnapshotError` when the
    snapshot has no state of the stacklet, a file is there already, or a
    file cannot be read or written; then `target` may hold part of the
    state, which the caller moves aside before it calls again.

        clone_back(snapshot, "docs", Path("~/famstack-data/docs"))   # True on APFS
    """
    source = Path(snapshot.path) / stacklet_id
    if not source.is_dir():
        raise SnapshotError(f"the snapshot {snapshot.name} holds no state of {stacklet_id}")
    volume = volume or ThisVolume()
    target = Path(target)
    try:
        target.mkdir(parents=True, exist_ok=True)
        cloned = volume.can_clone(target)
        _clone_into(source, target, volume if cloned else None)
    except OSError as error:
        raise SnapshotError(f"the state of {stacklet_id} cannot be cloned back from "
                            f"the snapshot {snapshot.name}: {error}") from error
    return cloned


def archive_paths(stacklet_dir: Path, data_root: Path) -> list[Path]:
    """The `[[backup.archive]]` sources of the stacklet in `stacklet_dir`,
    as paths in its data folder (`<data_root>/<id>/`).

    A path is declared with `{data_dir}` for the data folder. A source
    outside the stacklet's data folder is left out, because the snapshot
    does not hold it anyway. Empty for a stacklet that declares none.
    Raises `SnapshotError` when `stacklet.toml` cannot be read.

        # stacklets/docs/stacklet.toml:
        #   [[backup.archive]]
        #   path = "{data_dir}/docs/paperless/media"
        archive_paths(Path("stacklets/docs"), Path("~/famstack-data"))
        # [Path("paperless/media")]
    """
    stacklet_dir, data_root = Path(stacklet_dir), Path(data_root)
    try:
        with open(stacklet_dir / "stacklet.toml", "rb") as f:
            declared = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SnapshotError(f"{stacklet_dir / 'stacklet.toml'}: cannot be read: {error}") from error
    own = data_root / declared.get("id", stacklet_dir.name)
    paths = []
    for source in (declared.get("backup") or {}).get("archive") or []:
        path = Path(str(source.get("path", "")).replace("{data_dir}", str(data_root)))
        if path.is_relative_to(own) and path != own:
            paths.append(path.relative_to(own))
    return paths


# ── What a snapshot holds ────────────────────────────────────────────────

@dataclass(frozen=True)
class _Part:
    """One folder of the snapshot: `target` is its name in the snapshot,
    `source` the folder it holds, `items` the entries of `source` it
    takes (None: all), `leave_out` the paths in `source` it skips."""

    target: str
    source: Path
    items: tuple[str, ...] | None = None
    leave_out: tuple[Path, ...] = ()


def _parts(data_root: Path, instance_dir: Path, stacklets: dict[str, Path]) -> list[_Part]:
    """The folders of a snapshot: one for each stacklet's data folder, and
    `stack/` for the configuration."""
    parts = [_Part(sid, data_root / sid, leave_out=tuple(archive_paths(path, data_root)))
             for sid, path in stacklets.items()]
    return [*parts, _Part(CONFIG_FOLDER, instance_dir, items=_CONFIG_ITEMS)]


def _files(part: _Part):
    """Each entry that `part` holds, as `(path in source, kind)`, parents
    before children. The kind is "dir", "file" or "link". A socket or a
    pipe is no state and is left out, and so is each path in `leave_out`
    with everything below it."""
    if not part.source.is_dir():
        return
    tops = part.items if part.items is not None else sorted(os.listdir(part.source))
    for top in tops:
        yield from _walk(part.source, Path(top), set(part.leave_out))


def _walk(root: Path, relative: Path, leave_out: set[Path]):
    """`_files` below `root / relative`."""
    if relative in leave_out:
        return
    path = root / relative
    if path.is_symlink():
        yield relative, "link"
    elif path.is_dir():
        yield relative, "dir"
        for name in sorted(os.listdir(path)):
            yield from _walk(root, relative / name, leave_out)
    elif path.is_file():
        yield relative, "file"


def _check_room(parts: list[_Part], folder: Path, volume: Volume) -> None:
    """Refuse a copy that does not fit on the volume, naming both sizes."""
    needed = sum((part.source / relative).stat().st_size
                 for part in parts for relative, kind in _files(part) if kind == "file")
    free = volume.free_bytes(folder)
    if needed > free:
        raise SnapshotError(f"the data folder is not on APFS, so the snapshot is a copy. "
                            f"The copy needs {_size(needed)} and the disk has {_size(free)} free")


# ── Writing a snapshot ───────────────────────────────────────────────────

def _snapshots_folder(data_root: Path, volume: Volume) -> Path:
    """`.snapshots/` in `data_root`; made, and excluded from Time Machine,
    when it is missing."""
    folder = data_root / SNAPSHOTS_FOLDER
    if not folder.is_dir():
        try:
            folder.mkdir(parents=True)
        except OSError as error:
            raise SnapshotError(f"{folder}: cannot be made: {error}") from error
        volume.exclude_from_backups(folder)
    return folder


def _write(folder: Path, name: str, parts: list[_Part], record: Manifest,
           volume: Volume | None) -> Snapshot:
    """Write the snapshot `name` into `folder`: the parts into a `.taking-`
    folder, the manifest last, then the rename. Clones with `volume`, or
    copies when it is None. Any failure, also Ctrl-C, removes the
    `.taking-` folder. The manifest comes before the rename, so a folder
    with a snapshot's name always holds its manifest."""
    partial, final = folder / f"{_TAKING}{name}", folder / name
    renamed = False
    try:
        partial.mkdir()
        for part in parts:
            _write_part(part, partial / part.target, volume)
        manifests.write(partial, record)
        partial.rename(final)
        renamed = True
    except (OSError, ManifestError) as error:
        raise SnapshotError(f"the snapshot {name} stopped halfway and was removed: {error}") from error
    finally:
        if not renamed:
            # Safe to delete: this folder was made by this take, above, and
            # holds only what it wrote. The data folders are never touched.
            shutil.rmtree(partial, ignore_errors=True)
    return Snapshot(name, final, record)


def _write_part(part: _Part, target: Path, volume: Volume | None) -> None:
    """Clone or copy the entries of `part` into `target`. The folders get
    their mode and times last, so a read-only folder still takes its
    files."""
    target.mkdir()
    folders = []
    for relative, kind in _files(part):
        source, copy = part.source / relative, target / relative
        if kind == "dir":
            copy.mkdir()
            folders.append((source, copy))
        elif kind == "link":
            copy.symlink_to(os.readlink(source))
        elif volume is not None:
            volume.clone(source, copy)
        else:
            shutil.copy2(source, copy, follow_symlinks=False)
    for source, copy in reversed(folders):
        shutil.copystat(source, copy, follow_symlinks=False)


def _clone_into(source: Path, target: Path, volume: Volume | None) -> None:
    """`clone_back` below `source`: each entry into `target`, folders
    entered when they are there. A file or a link there already raises
    `FileExistsError`. Folders get their mode and times last, as in
    `_write_part`."""
    for name in sorted(os.listdir(source)):
        entry, copy = source / name, target / name
        if entry.is_symlink() or not entry.is_dir():
            if copy.is_symlink() or copy.exists():
                # It may be a family's file: a restore never writes over one.
                raise FileExistsError(errno.EEXIST, "is there already, and a restore "
                                      "never replaces a file", str(copy))
            if entry.is_symlink():
                copy.symlink_to(os.readlink(entry))
            elif entry.is_file():
                _clone_file(entry, copy, volume)
            continue
        copy.mkdir(exist_ok=True)
        _clone_into(entry, copy, volume)
        shutil.copystat(entry, copy, follow_symlinks=False)


def _clone_file(source: Path, target: Path, volume: Volume | None) -> None:
    """Clone `source` as `target` with `volume`, or copy it when None."""
    if volume is not None:
        volume.clone(source, target)
    else:
        shutil.copy2(source, target, follow_symlinks=False)


def _free_name(folder: Path, now: datetime) -> str:
    """The name of a new snapshot taken at `now`: its time in UTC, with
    "-2", "-3", … when a snapshot of that minute exists."""
    base = now.astimezone(timezone.utc).strftime(_NAME_TIME)
    name, number = base, 1
    while (folder / name).exists() or (folder / f"{_TAKING}{name}").exists():
        number += 1
        name = f"{base}-{number}"
    return name


# ── Names and sizes ──────────────────────────────────────────────────────

def _time_of(name: str) -> datetime:
    """The time in a snapshot's name.

        >>> _time_of("2026-11-24T2114Z-2")
        datetime.datetime(2026, 11, 24, 21, 14, tzinfo=datetime.timezone.utc)
    """
    match = _NAME.match(name)
    if match is None:
        raise ValueError(f"not a snapshot's name: {name}")
    return datetime.strptime(match.group(1), _NAME_TIME).replace(tzinfo=timezone.utc)


def _order_of(name: str) -> tuple[datetime, int]:
    """The sort key of a snapshot's name: its time, then its number."""
    match = _NAME.match(name)
    return _time_of(name), int(match.group(2) or 1) if match else 1


def _size(count: int) -> str:
    """A size in bytes for a person.

        >>> _size(1_500_000_000), _size(300_000_000), _size(512)
        ('1.5 GB', '300.0 MB', '512 bytes')
    """
    for unit, factor in (("GB", 10**9), ("MB", 10**6)):
        if count >= factor:
            return f"{count / factor:.1f} {unit}"
    return f"{count} bytes"


_LIBC: list = []


def _clonefile():
    """`clonefile(2)` from the C library, or None on a system without it."""
    if not _LIBC:
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            _LIBC.append(getattr(libc, "clonefile", None))
        except OSError:
            _LIBC.append(None)
    return _LIBC[0]
