"""The folders of a copy of the family memory on disk.

Git keeps files, not folders: a folder exists in the memory while a file
is in it. A copy on disk keeps a folder after its last file went, unless
whatever removed the file removes the folder too. Every writer of a copy
(the curator's card store, its mirror into the brain, the wiki's compile)
removes files with `remove_file`, and every reader that asks whether a
folder is there asks `holds_a_file`, so an empty folder left behind by an
older release reads as gone as well.

    remove_file(repo, "family/nahprojekte/notes/2026/10/tent.md")   # and the empty folders
    holds_a_file(repo / "family" / "nahprojekte")                    # False once it is empty

Standard library only, like `stack.cards`: the host CLI imports it.
"""

from __future__ import annotations

from pathlib import Path


def holds_a_file(folder: Path) -> bool:
    """Whether `folder` holds a file at any depth, as git would have the folder."""
    if not folder.is_dir():
        return False
    return any(path.is_file() for path in folder.rglob("*"))


def remove_file(root: Path, path: str) -> None:
    """Delete the file at `path` under `root`, and each folder that leaves empty.

    The folders are removed upwards from the file and stop at the first one
    that still holds something, and always at `root`. A missing file is no
    error: a removal applied twice leaves the same tree.
    """
    target = root / path
    target.unlink(missing_ok=True)
    for folder in target.parents:
        if folder == root or root not in folder.parents:
            return
        try:
            folder.rmdir()
        except OSError:
            return
