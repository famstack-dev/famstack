"""Whether oMLX is installed with Homebrew on this Mac: the one check for it.

    from stacklets.ai import homebrew_omlx
    homebrew_omlx.installed()    # True after `brew install jundot/omlx/omlx`

The ai stacklet's own oMLX comes from Homebrew (`hooks/on_install.py`). An
admin may have installed that formula before the setup, by hand or with
an earlier release, without running it. `stack up ai` installs the
formula only when it is missing, and then sets it up and starts it as the
stack's oMLX.

`brew` is looked up on PATH, then at Homebrew's own location on Apple
Silicon (`BREW`): a hook started under launchd has a PATH without
Homebrew.

What it does not do: look for the oMLX app in Applications, or ask a
running server.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

__all__ = ["LIST_COMMAND", "BREW", "installed"]

# What asks Homebrew for the formula; it exits 0 when it is installed.
# Read at each call, so a test points it at a stand-in: the Mac that runs
# the suite may have oMLX from Homebrew itself.
LIST_COMMAND = ("brew", "list", "--versions", "omlx")

# `brew list` reads its own folders and answers in about a second; a
# Homebrew that hangs must not hold up the setup's page for long.
LIST_SECONDS = 15

# Where Homebrew installs `brew` on Apple Silicon, for a PATH without it.
# Read at each call, so a test points it at a stand-in.
BREW = Path("/opt/homebrew/bin/brew")


def installed() -> bool:
    """Whether Homebrew lists the oMLX formula. False without Homebrew, and
    when `brew` fails or takes longer than `LIST_SECONDS`. Reads only.

        installed()    # False on a Mac where `brew list --versions omlx` exits 1
    """
    brew = _brew()
    if brew is None:
        return False
    try:
        listed = subprocess.run((brew, *LIST_COMMAND[1:]), capture_output=True,
                                timeout=LIST_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return listed.returncode == 0


def _brew() -> str | None:
    """The `brew` to run: the one on PATH, else Homebrew's own (`BREW`)
    when it is there, else None."""
    if found := shutil.which(LIST_COMMAND[0]):
        return found
    return str(BREW) if BREW.is_file() else None
