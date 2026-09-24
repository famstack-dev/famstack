"""The global `famstack` command on PATH, next to `./stack`.

`./stack` finds its checkout with `dirname "$0"`, so a symlink to it
would look for `lib/` next to the symlink. The global command is a
small script that execs the checkout's launcher instead.

It names only the checkout. Which instance a command runs against stays
the CLI's decision (`STACK_DIR`, else the checkout), so one script serves
every instance the caller selects.

The name is fixed, not taken from `[core] name`: docs, hook messages
and support have to be able to state the command, and renaming the
product in one instance must not rename the command under it.

A command of the same name that is not ours is never touched: install
leaves it in place, uninstall leaves it in place, and the caller falls
back to printing `./stack`.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

NAME = "famstack"

FREE = "free"        # nothing answers to the name
OURS = "ours"        # the script for this checkout answers to it
FOREIGN = "foreign"  # something else does, or something else sits in the way


def script(checkout: Path) -> str:
    """The script installed for `checkout`, byte for byte.

    Ownership is decided by comparing a file with this text, so the text
    must only depend on the checkout.
    """
    launcher = shlex.quote(str(Path(checkout).resolve() / "stack"))
    return (
        "#!/bin/sh\n"
        "# Installed by the stack installer; its uninstall removes it.\n"
        f'exec {launcher} "$@"\n'
    )


def bin_dir() -> Path | None:
    """Homebrew's bin directory, or None without Homebrew.

    The stack requires Homebrew, whose bin directory is on its users'
    PATH and writable without sudo. The prefix differs between machines,
    so it is asked for rather than assumed.
    """
    brew = shutil.which("brew")
    if not brew:
        return None
    try:
        result = subprocess.run([brew, "--prefix"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    prefix = result.stdout.strip()
    return Path(prefix) / "bin" if result.returncode == 0 and prefix else None


def state(checkout: Path, directory: Path, path: str | None = None) -> str:
    """Who answers to the name: FREE, OURS or FOREIGN.

    `path` is the search path, defaulting to this process's PATH. A
    command earlier on it shadows ours, so it counts as foreign even
    when our script sits in `directory`.
    """
    target = directory / NAME
    found = shutil.which(NAME, path=path)
    if found and os.path.realpath(found) != os.path.realpath(target):
        return FOREIGN
    if os.path.lexists(target):
        return OURS if is_ours(target, checkout) else FOREIGN
    return FREE


def is_ours(target: Path, checkout: Path) -> bool:
    try:
        return not target.is_symlink() and target.read_text() == script(checkout)
    except (OSError, UnicodeDecodeError):
        return False


def install(checkout: Path, directory: Path, path: str | None = None) -> bool:
    """Put the command in `directory` for `checkout` if the name is free.

    Returns True when the command runs this checkout afterwards. Idempotent:
    a second call finds the script in place and writes nothing.
    """
    current = state(checkout, directory, path)
    if current != FREE:
        return current == OURS
    target = directory / NAME
    try:
        target.write_text(script(checkout))
        target.chmod(0o755)
    except OSError:
        return False
    return True


def uninstall(checkout: Path, directory: Path) -> bool:
    """Remove the command from `directory` if it is the script for `checkout`.

    Returns True when something was removed.
    """
    target = directory / NAME
    if not is_ours(target, checkout):
        return False
    try:
        target.unlink()
    except OSError:
        return False
    return True
