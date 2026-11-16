"""config_file.py: change stack.toml while other processes may change it too.

    from stack.config_file import rewrite

    rewrite(instance_dir / "stack.toml", lambda text: text + '[backup]\\nenabled = true\\n')

stack.toml is written by more than one process at a time: a stacklet's
preparation runs outside the queue of runs (`stack.runs`) and writes its
settings while another run writes its own. A write reads the file,
changes it and writes it back, so two writes at once would each write
back a file without the other's change, or interleave into a file that
does not parse.

`rewrite` is the one way to change the file after the installer created
it:

- **One writer at a time.** The read, the change and the write happen
  under a lock of their own (`.stack/stack.toml.lock` beside the file),
  held for that moment only. It is not a run lock: no run waits for it.
- **Readers see the old file or the new one.** The new text goes to a
  temporary file in the same folder, which replaces the old one in one
  rename. A write that fails leaves the old file and no temporary file.
- **The admin's file stays where it is.** A stack.toml behind a symlink
  is written where the link points, with its mode.

Callers: `Stack._set_cfg` (one value, for hooks and commands) and the
backup stacklet's `write_target` (a whole `[backup.targets.<name>]`
block). Not here: what a change does to the text.
"""

from __future__ import annotations

import fcntl
import os
import shutil
from pathlib import Path
from typing import Callable

__all__ = ["rewrite", "LOCK"]

# The lock, relative to the folder that holds stack.toml.
LOCK = Path(".stack") / "stack.toml.lock"


def rewrite(toml_path: Path, change: Callable[[str], str]) -> None:
    """Replace the text of `toml_path` with `change(text)`, under the lock.

    Reads the file (an empty text when it does not exist yet), calls
    `change` with it and writes what it returns. Raises what `change` or
    the write raises; the file is then as it was.

        rewrite(Path("/Users/homer/famstack/stack.toml"), str.upper)
        # every letter of stack.toml upper case, nothing else changed
    """
    lock = toml_path.parent / LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a", encoding="utf-8") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        target = toml_path.resolve()
        text = target.read_text() if target.exists() else ""
        _replace(target, change(text))


def _replace(target: Path, text: str) -> None:
    """Write `text` to `target` through a temporary file in its folder,
    removed again when the write fails."""
    written = target.with_name(f".{target.name}.{os.getpid()}")
    try:
        written.write_text(text)
        if target.exists():
            shutil.copymode(target, written)
        written.replace(target)
    finally:
        written.unlink(missing_ok=True)
