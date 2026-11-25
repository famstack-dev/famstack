"""The volume of the data folder: clone a file, measure free space, skip Time Machine.

The snapshot store (`stack.snapshot.store`) and the rollback's restore
(`stack.snapshot.rollback_steps`) reach the file system through the port
`Volume`. `ThisVolume` is the real one:

    from stack.snapshot.volume import ThisVolume

    volume = ThisVolume()
    volume.can_clone(Path("~/famstack-data/.snapshots"))   # True on APFS
    volume.clone(Path("a"), Path("b"))                     # b is a clone of a
    volume.free_bytes(Path("~/famstack-data"))             # 412_000_000_000

Why a port: a test can stand in a volume that does not clone, is full
or fails to clone, without a second disk. The store decides what to
clone and what to refuse. This module only does the file system's
part.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

__all__ = ["ThisVolume", "Volume"]


class Volume(Protocol):
    """The file system of the data folder, as the store uses it.

    `ThisVolume` is the real one. A test stands in a volume that does not
    clone, is full or fails to clone:

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
    `tmutil addexclusion`. Without `tmutil` it does nothing.
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
