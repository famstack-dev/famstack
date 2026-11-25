"""The error of the snapshot package.

    from stack.snapshot.errors import SnapshotError

    raise SnapshotError("docs still runs: stop it before its snapshot")

Every module of `stack.snapshot` raises it when it refuses or fails, and
every command prints its message. It has a module of its own, because
two modules raise it and one imports the other. The store
(`stack.snapshot.store`) imports the files of a snapshot
(`stack.snapshot.files`).
"""

from __future__ import annotations

__all__ = ["SnapshotError"]


class SnapshotError(Exception):
    """A snapshot or a rollback that the stack refused or could not do.
    The message says why, and names the stacklet or the size.

        SnapshotError("docs still runs: stop it before its snapshot")
    """
