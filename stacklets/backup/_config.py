"""Target config read/write for the backup stacklet's stack.toml entries.

The framework's ``ctx.cfg`` reads and writes a stacklet's own section
(``[backup]``, in our case), but backup's target config lives one level
deeper at ``[backup.targets.<name>]``. ``ctx.cfg`` can't address nested
tables, so this module provides narrow, atomic helpers scoped to that
schema only.

We deliberately do *not* try to be a generalized TOML editor:

* The reader uses ``tomllib`` (stdlib) so it benefits from a real
  parser — no surprises with multi-line strings or array tables.
* The writer is a targeted block replacement: it finds the
  ``[backup.targets.<name>]`` header and replaces from there until the
  next section. Comments and content outside the target block are
  preserved byte-for-byte.

If multiple stacklets eventually need the same kind of nested-table
write, this is the natural seed for a framework-level helper. Until
then, keeping the surface narrow protects the rest of stack.toml from
us.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from stack.config_file import rewrite

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover — py < 3.11 fallback
    from stack._vendor import tomli as tomllib  # type: ignore


def read_target(toml_path: Path, target_name: str) -> Optional[dict]:
    """Read ``[backup.targets.<target_name>]`` and return its config.

    Returns ``None`` when the file is missing, unreadable, or the
    target isn't configured. Doesn't distinguish those cases at the
    return value — callers that need the distinction can check
    ``toml_path.exists()`` themselves.
    """
    if not toml_path.exists():
        return None
    try:
        with toml_path.open("rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        return None
    return data.get("backup", {}).get("targets", {}).get(target_name)


def write_target(toml_path: Path, target_name: str, config: dict) -> None:
    """Create or replace ``[backup.targets.<target_name>]`` atomically.

    All values in ``config`` are written as TOML basic strings (double-
    quoted, with backslash + quote escaped). Int and bool aren't
    supported because no current target field needs them — adding them
    later is a one-line change in :func:`_render_value`.

    Comment handling: comments and blank lines that visually precede the
    *next* section header are left attached to that section, not
    swallowed into the replaced block. Comments *inside* the replaced
    block (between key lines) are lost — replacing means replacing.

    The write goes through ``stack.config_file.rewrite``: under the lock
    every writer of stack.toml takes, and through a temporary file that
    replaces stack.toml in one rename.
    """
    new_block = _render_block(target_name, config)
    rewrite(toml_path, lambda content: _with_block(content, target_name, new_block))


def _with_block(content: str, target_name: str, new_block: str) -> str:
    """``content`` with the target's block replaced by ``new_block``, or
    with ``new_block`` appended when the target has none."""
    bounds = _find_block(content, target_name)
    if bounds is None:
        return _append_block(content, new_block)
    start, end = bounds
    return content[:start] + new_block + content[end:]


# ── Internals ──────────────────────────────────────────────────────────────

def _render_block(target_name: str, config: dict) -> str:
    """Render a ``[backup.targets.<name>]`` block with given values."""
    lines = [f"[backup.targets.{target_name}]"]
    for k, v in config.items():
        lines.append(f"{k} = {_render_value(v)}")
    return "\n".join(lines) + "\n"


def _render_value(value) -> str:
    """Serialize a Python value as a TOML literal. Strings only for now."""
    return f'"{_toml_escape(str(value))}"'


def _toml_escape(s: str) -> str:
    """Escape backslashes and double quotes for TOML basic strings.

    Other control characters aren't escaped here — none of the target
    config fields (engine name, disk name, cron schedule) plausibly
    contain newlines or tabs. If that assumption changes, replace
    this with a fuller escape table.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _find_block(content: str, target_name: str) -> Optional[tuple]:
    """Locate ``[backup.targets.<name>]`` in ``content``.

    Returns ``(start_offset, end_offset)`` for slicing — start is the
    beginning of the header line; end is just past the last key=value
    line of the block. Trailing blank lines and comments that visually
    attach to the *next* section are NOT consumed; they stay where
    they were so writing one target can't accidentally orphan another
    target's lead-in comment.

    Returns ``None`` when the header isn't present.
    """
    header = f"[backup.targets.{target_name}]"
    lines = content.splitlines(keepends=True)

    start_line = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start_line = i
            break
    if start_line is None:
        return None

    # Walk forward. A "data line" (key = value) extends the block; a
    # blank or comment line does not — those belong to whatever
    # follows. Stop at the next section header.
    last_data_line = start_line
    cursor = start_line + 1
    while cursor < len(lines):
        stripped = lines[cursor].strip()
        if stripped.startswith("["):
            break
        if stripped and not stripped.startswith("#"):
            last_data_line = cursor
        cursor += 1

    end_line = last_data_line + 1
    start_offset = sum(len(line) for line in lines[:start_line])
    end_offset = sum(len(line) for line in lines[:end_line])
    return start_offset, end_offset


def _append_block(content: str, new_block: str) -> str:
    """Append a block to file content, ensuring one blank line of
    separation from whatever came before."""
    if not content:
        return new_block
    stripped = content.rstrip("\n")
    return f"{stripped}\n\n{new_block}"
