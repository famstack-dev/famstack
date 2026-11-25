"""The record of a restart whose snapshot failed, kept until a snapshot is whole.

A restart records what a stacklet runs while it runs, stops it, and then
takes the snapshot (`stack.snapshot.release`). When the take fails, the
stacklet is stopped and the record is all that is left of what it ran:
a stopped container drops out of `docker ps`, and a stacklet without
containers loses its `.stack/<id>.up` stamp at the stop. So the restart
keeps the record in `.stack/snapshot-pending/<id>/MANIFEST.json` before
it stops anything, and removes it once the snapshot is whole. While a
record waits, the stacklet does not start on the new release:

    pending.keep(instance_dir, record)          # before the stop
    pending.waiting(instance_dir, "docs")       # True: `stack up docs` refuses
    pending.read(instance_dir, ["docs"])        # the record, for the next take
    pending.clear(instance_dir, ["docs"])       # the snapshot is whole, the admin
                                                # said --no-snapshot, or a rollback
                                                # restored the stacklet

Why it works this way:

- **The record, not Docker, is the memory of the failed restart.** A
  retry that asked Docker again would find no commit for a stopped
  stacklet without containers, and the record taken while it ran names
  `up: true` and the images that ran, which a record after the stop
  cannot say.
- **One record per stacklet**, so the admin can retry one stacklet of a
  failed restart of several. Each file is a manifest with one stacklet
  record, in the format of `stack.manifest`.
- **Kept before the stop.** A record that cannot be kept (a full disk)
  stops nothing, so no stacklet is left stopped without one.
- **A whole snapshot, `--no-snapshot`, `stack destroy` or a rollback
  ends the wait.** A stacklet that did not stop still runs what it ran,
  so its record goes too. A rollback restores the state of a snapshot,
  so the record of the newer release no longer applies. Nothing else
  removes it.

This module keeps and reads the records; `stack.snapshot.release` decides
when, and `stack.cli` refuses the start while one waits.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from .. import manifest as manifests
from ..manifest import Manifest, ManifestError
from .store import SnapshotError

__all__ = ["PENDING_FOLDER", "Unreadable", "clear", "keep", "read", "waiting"]

PENDING_FOLDER = "snapshot-pending"
"""The folder in `.stack/` that holds the records waiting for a snapshot."""


class Unreadable(SnapshotError):
    """The kept record of `stacklet` at `path` cannot be read, so no
    snapshot can be taken from it.

        Unreadable("docs", path, f"{path}: not a readable manifest: …")
    """

    def __init__(self, stacklet: str, path: Path, why: str):
        super().__init__(f"the record of the failed snapshot of {stacklet} cannot be read: {why}")
        self.stacklet, self.path = stacklet, path


def keep(instance_dir: Path, record: Manifest) -> None:
    """Keep `record` for each of its stacklets, until a snapshot is whole.

    Raises `OSError` when a record cannot be written.

        keep(Path("~/famstack"), Manifest(..., stacklets={"docs": …}))
        # .stack/snapshot-pending/docs/MANIFEST.json
    """
    for sid, stacklet in record.stacklets.items():
        manifests.write(_folder(instance_dir) / sid,
                        dataclasses.replace(record, stacklets={sid: stacklet}))


def waiting(instance_dir: Path, stacklet_id: str) -> bool:
    """Whether the stacklet waits for its snapshot after a failed take.

        waiting(Path("~/famstack"), "docs")    # True until a snapshot is whole
    """
    return (_folder(instance_dir) / stacklet_id / manifests.MANIFEST_FILE).is_file()


def read(instance_dir: Path, ids: list[str]) -> Manifest | None:
    """The kept records of the stacklets among `ids`, as one manifest, or
    None when none of them waits.

    Raises `Unreadable` when a record cannot be read: then the restart
    stops before it changes anything.

        read(Path("~/famstack"), ["docs", "photos"]).stacklets   # {"docs": …}
    """
    found = []
    for sid in ids:
        try:
            record = manifests.read(_folder(instance_dir) / sid)
        except ManifestError as error:
            raise Unreadable(sid, _folder(instance_dir) / sid / manifests.MANIFEST_FILE,
                             str(error)) from error
        if record is not None:
            found.append(record)
    if not found:
        return None
    stacklets = {sid: r for record in found for sid, r in record.stacklets.items()}
    return dataclasses.replace(found[0], stacklets=stacklets)


def clear(instance_dir: Path, ids: list[str]) -> None:
    """Remove the kept records of `ids`: a snapshot is whole, the admin
    restarts without one, or a rollback restored them. A stacklet with no
    record is left as it is.

        clear(Path("~/famstack"), ["docs"])
    """
    # Only the record file and its folder go, never a whole tree: an id
    # comes from the command line.
    for sid in ids:
        folder = _folder(instance_dir) / sid
        (folder / manifests.MANIFEST_FILE).unlink(missing_ok=True)
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()


def _folder(instance_dir: Path) -> Path:
    return Path(instance_dir) / ".stack" / PENDING_FOLDER
