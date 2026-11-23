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

`read_stack_toml` is the one strict read: a file that exists and does not
parse stops the command (`StackTomlUnreadable`) instead of reading as empty.

Callers: `Stack._set_cfg` (one value, for hooks and commands) and the
backup stacklet's `write_target` (a whole `[backup.targets.<name>]`
block). Not here: what a change does to the text.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import tomllib
from pathlib import Path
from typing import Callable

__all__ = ["rewrite", "LOCK", "read_stack_toml", "StackTomlUnreadable", "configured_data_dir"]

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


# ── Reading ──────────────────────────────────────────────────────────────

class StackTomlUnreadable(Exception):
    """stack.toml exists and cannot be read as TOML.

    The message names the file and carries the parser's own text, which
    holds the line and column. The command stops with it and starts nothing.
    """


def read_stack_toml(path: Path) -> dict:
    """The table a stack.toml holds; empty when the file does not exist yet.

    A file that exists and does not parse raises `StackTomlUnreadable`.
    Reading it as empty would send the stack to the default data folder,
    where the stacklets would start on no data and the family's data would
    look gone. `adopt_product_name` would also write over the broken file.
    """
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as e:
        raise StackTomlUnreadable(
            f"{path} does not parse, nothing was started or changed: {e}. "
            "Fix that line, or restore the file from a backup.") from e


def configured_data_dir(config: dict) -> Path:
    """The data dir a stack.toml names: `[core] data_dir`, else `~/<name>-data`."""
    core = config.get("core", {})
    return Path(core.get("data_dir", f"~/{core.get('name', 'stack')}-data")).expanduser()
