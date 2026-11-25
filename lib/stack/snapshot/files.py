"""The files of a snapshot: what it holds, and how they are written and cloned back.

    from stack.snapshot import files

    parts = files.parts(data_root, instance_dir, {"docs": Path("stacklets/docs")})
    files.check_room(parts, data_root / ".snapshots", volume)   # a copy that does not fit
    files.write_part(parts[0], taking / "docs", volume)          # clone, or copy with None
    files.clone_back(snapshot.path, snapshot.name, "docs", data_root / "docs")   # restore
    files.archive_paths(Path("stacklets/docs"), data_root)       # [Path("paperless/media")]

A snapshot holds one folder for each stacklet's data folder. Its folder
`stack/` holds the configuration (`stack.toml`, `users.toml`, `.stack/`).
The store (`stack.snapshot.store`) decides where they go, when a
snapshot is whole and what its name is. This module only reads and
writes the files.

Why it works this way:

- **A file is a clone when the volume can clone.** On APFS a clone
  (`clonefile(2)`, the call of `cp -c`) takes no space until one of the
  two files changes. A change of the live file never changes the
  snapshot. On another file system the files are copied. `check_room`
  refuses when the disk has too little free space, because a copy needs
  the space. It names the size.
- **The large files are left out.** Each stacklet declares them as
  `[[backup.archive]]` sources in its `stacklet.toml` (the photo
  library, the document originals). A rollback never replaces them, and a
  copy of them could fill the disk.
- **A restore never writes over a file.** `clone_back` enters a folder
  that is there. It refuses a file or a link that is there, because it
  can be the family's file.
- **Folders get their mode and times last,** because a read-only folder
  must still take its files.

The file system is behind the port `Volume` (`stack.snapshot.volume`).

Debt: `[[backup.archive]]` has two readers. `archive_paths` here resolves
the paths for the snapshot. The backup stacklet resolves them for its
sync, because the framework never imports a stacklet's code. Both
replace only `{data_dir}`, and nothing checks that they agree. Trigger: a
second template variable in an archive path, or a restore in the backup
stacklet that reads the snapshot's folder.
"""

from __future__ import annotations

import errno
import os
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import SnapshotError
from .volume import ThisVolume, Volume

__all__ = [
    "CONFIG_FOLDER",
    "Part",
    "archive_paths",
    "check_room",
    "clone_back",
    "parts",
    "size_text",
    "write_part",
]

CONFIG_FOLDER = "stack"
"""The folder in a snapshot that holds the stack's configuration."""

# What the configuration folder holds, from the instance folder. A file
# that the instance does not have is left out.
_CONFIG_ITEMS = ("stack.toml", "users.toml", ".stack")


# ── What a snapshot holds ────────────────────────────────────────────────

@dataclass(frozen=True)
class Part:
    """One folder of the snapshot.

    `target` is its name in the snapshot. `source` is the folder it holds.
    `items` are the entries of `source` that it takes (None: all).
    `leave_out` are the paths in `source` that it skips.

        Part("docs", Path("~/famstack-data/docs"),
             leave_out=(Path("paperless/media"),))
        Part("stack", Path("~/famstack"), items=("stack.toml", "users.toml", ".stack"))
    """

    target: str
    source: Path
    items: tuple[str, ...] | None = None
    leave_out: tuple[Path, ...] = ()


def parts(data_root: Path, instance_dir: Path, stacklets: dict[str, Path]) -> list[Part]:
    """The folders of a snapshot: one for each stacklet's data folder, and
    `stack/` for the configuration. Reads each stacklet's
    `[[backup.archive]]` sources (`archive_paths`) for what it leaves out.

        parts(Path("~/famstack-data"), Path("~/famstack"), {"docs": Path("stacklets/docs")})
        # [Part("docs", …/docs, leave_out=(Path("paperless/media"),)),
        #  Part("stack", ~/famstack, items=("stack.toml", "users.toml", ".stack"))]
    """
    parts = [Part(sid, data_root / sid, leave_out=tuple(archive_paths(path, data_root)))
             for sid, path in stacklets.items()]
    return [*parts, Part(CONFIG_FOLDER, instance_dir, items=_CONFIG_ITEMS)]


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


def check_room(parts: list[Part], folder: Path, volume: Volume) -> None:
    """Refuse a copy that does not fit on the volume, naming both sizes.
    Raises `SnapshotError` when the files of `parts` need more than the
    free space of the volume that holds `folder`. Writes nothing.

        check_room(parts, Path("~/famstack-data/.snapshots"), volume)
        # SnapshotError: … The copy needs 2.1 GB and the disk has 1.5 GB free
    """
    needed = sum((part.source / relative).stat().st_size
                 for part in parts for relative, kind in _files(part) if kind == "file")
    free = volume.free_bytes(folder)
    if needed > free:
        raise SnapshotError(f"the data folder is not on APFS, so the snapshot is a copy. "
                            f"The copy needs {size_text(needed)} and the disk has {size_text(free)} free")


def size_text(count: int) -> str:
    """A byte count as text for a person: GB, MB or bytes.
    The text uses the largest unit that fits.

        >>> size_text(1_500_000_000), size_text(300_000_000), size_text(512)
        ('1.5 GB', '300.0 MB', '512 bytes')
    """
    for unit, factor in (("GB", 10**9), ("MB", 10**6)):
        if count >= factor:
            return f"{count / factor:.1f} {unit}"
    return f"{count} bytes"


# ── Writing and cloning back ─────────────────────────────────────────────

def write_part(part: Part, target: Path, volume: Volume | None) -> None:
    """Clone or copy the entries of `part` into `target`. The folders
    get their mode and times last, because a read-only folder must still
    take its files. Clones with `volume`, or copies when it is None.
    `target` must not exist yet. Raises `OSError` when a file cannot be
    read or written.

        write_part(parts[0], Path("…/.snapshots/.taking-2026-11-24T2114Z/docs"), volume)
        # …/docs/postgres/…, …/docs/paperless/data/… (no paperless/media/)
    """
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


def clone_back(folder: Path, name: str, stacklet_id: str, target: Path,
               volume: Volume | None = None) -> bool:
    """Clone the state of `stacklet_id` from a snapshot into `target`.
    Return whether the files are clones. `folder` is the snapshot's
    folder, and `name` its name for the messages. `target` is the
    stacklet's data folder.

    `target` holds only what the snapshot left out: the rollback moved
    the rest aside before. Each entry of the snapshot is cloned into
    `target`. A folder that is there already (the parent of an
    `[[backup.archive]]` source) is entered, and its other entries stay. A
    file or a link that is there already is never replaced: the call
    refuses. The snapshot is only read.

    Raises `SnapshotError` when the snapshot has no state of the
    stacklet. It raises it also when a file is there already or cannot
    be read or written. After such a failure, `target` may hold part of
    the state. The caller moves that part aside before it calls again.

        clone_back(Path("~/famstack-data/.snapshots/2026-11-24T2114Z"), "2026-11-24T2114Z",
                   "docs", Path("~/famstack-data/docs"))   # True on APFS
    """
    source = Path(folder) / stacklet_id
    if not source.is_dir():
        raise SnapshotError(f"the snapshot {name} holds no state of {stacklet_id}")
    volume = volume or ThisVolume()
    target = Path(target)
    try:
        target.mkdir(parents=True, exist_ok=True)
        cloned = volume.can_clone(target)
        _clone_into(source, target, volume if cloned else None)
    except OSError as error:
        raise SnapshotError(f"the state of {stacklet_id} cannot be cloned back from "
                            f"the snapshot {name}: {error}") from error
    return cloned


# ── Walking and cloning the files ────────────────────────────────────────

def _files(part: Part):
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


def _clone_into(source: Path, target: Path, volume: Volume | None) -> None:
    """`clone_back` below `source`: each entry into `target`, folders
    entered when they are there. A file or a link there already raises
    `FileExistsError`. Folders get their mode and times last, as in
    `write_part`."""
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
