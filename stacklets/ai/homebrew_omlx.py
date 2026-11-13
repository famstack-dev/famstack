"""Whether oMLX is installed with Homebrew on this Mac: the one check for it.

    from stacklets.ai import homebrew_omlx
    homebrew_omlx.installed()    # True after `brew install jundot/omlx/omlx`
    homebrew_omlx.version()      # "0.8.0rc1", the release Homebrew installed
    homebrew_omlx.program()      # "/opt/homebrew/opt/omlx/bin/omlx"

The ai stacklet's own oMLX comes from Homebrew (`hooks/on_install.py`). An
admin may have installed that formula before the setup, by hand or with
an earlier release, without running it. `stack up ai` installs the
formula only when it is missing, and then runs the program as the
stack's oMLX in a folder of its own (`omlx_service.py`): the formula's
settings and service stay the admin's.

Homebrew installs the release its tap offers, which can be a release
candidate: the tap holds one formula and follows oMLX's newest tag. The
run says which release it installed (`version`), and the answer check
(`answer.py`) catches a build that cannot answer.

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

__all__ = ["LIST_COMMAND", "BREW", "installed", "version", "program"]

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


def version() -> str:
    """The release of the oMLX formula Homebrew has, as `brew list
    --versions omlx` names it; "" when it has none or cannot say.

        version()    # "0.8.0rc1"
    """
    brew = _brew()
    if brew is None:
        return ""
    try:
        listed = subprocess.run((brew, *LIST_COMMAND[1:]), capture_output=True, text=True,
                                timeout=LIST_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    words = listed.stdout.split()
    return words[-1] if listed.returncode == 0 and len(words) > 1 else ""


def program() -> str:
    """The oMLX binary of the formula, by Homebrew's own `opt` link beside
    the `brew` in use, which stays the same across upgrades. launchd runs
    it by this path, because its PATH has no Homebrew.

        program()    # "/opt/homebrew/opt/omlx/bin/omlx"
    """
    brew = _brew() or str(BREW)
    return str(Path(brew).parent.parent / "opt" / "omlx" / "bin" / "omlx")


def _brew() -> str | None:
    """The `brew` to run: the one on PATH, else Homebrew's own (`BREW`)
    when it is there, else None."""
    if found := shutil.which(LIST_COMMAND[0]):
        return found
    return str(BREW) if BREW.is_file() else None
