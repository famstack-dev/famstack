"""The oMLX app on this Mac: whether it runs, so famstack's oMLX does not.

    from .. import omlx_app

    omlx_app.running()             # True while the oMLX app runs
    omlx_app.refuse_while_running()   # raises Cancelled(QUIT_FIRST) then

The oMLX app (`oMLX.app`, a menu bar app) runs its own MLX server. Two MLX
servers on one Mac's graphics chip share its memory and can crash the
Mac. So famstack's oMLX is never installed or started while
the app runs: the run stops first, says to quit the app, and changes
nothing. The setup page shows that message on the AI's tile, as it shows
the end of any run, because the refusal is `Cancelled`, which the
framework records as the run's result.

One check, the app's process: `pgrep -f` for a command line inside the
app's bundle (`oMLX.app/Contents/`). That matches the app and the server
it starts from its bundle, wherever the app is installed, and never
famstack's oMLX, which Homebrew installs outside any app bundle. No port
is probed: a port says that something answers, not what it is.

What it does not do: quit the app, or look at an oMLX on another machine
(that one has its own graphics chip). The callers are the places that
install, start or restart famstack's oMLX: `hooks/on_install.py`,
`hooks/on_start.py` (every `stack up ai`, `stack ai switch managed`
included) and `cli/key.py` (`stack ai key new`, which returns the message
as its error). launchd starts famstack's oMLX at login without asking
this module.
"""

from __future__ import annotations

import subprocess

from stack.hooks import Cancelled

__all__ = ["IN_THE_APP", "PROCESS_COMMAND", "QUIT_FIRST", "running", "refuse_while_running"]

# Part of the command line of every process the app runs, the app and the
# server it starts from its bundle.
IN_THE_APP = "oMLX.app/Contents/"
# What finds the app's processes; `pgrep` exits 0 when one matches. Looked
# up on PATH and read at each call, so a test can stand in for it.
PROCESS_COMMAND = ("pgrep", "-f", IN_THE_APP)
# `pgrep` answers at once; the limit only keeps a hung one from holding
# the run.
PROCESS_SECONDS = 10

QUIT_FIRST = ("Quit the oMLX app first (its icon in the menu bar, then Quit), then run this "
              "again. Two AI servers on one Mac's graphics chip can crash the Mac. "
              "Nothing changed.")


def running() -> bool:
    """Whether a process of the oMLX app runs on this Mac. False when
    `pgrep` finds none or cannot be run. Reads only.

        running()    # True while oMLX.app is open, False after Quit
    """
    try:
        found = subprocess.run(PROCESS_COMMAND, capture_output=True, timeout=PROCESS_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return found.returncode == 0


def refuse_while_running() -> None:
    """Stop the run before famstack's oMLX is installed or started when
    the oMLX app runs: raises `Cancelled(QUIT_FIRST)`, which ends the run
    with that message and nothing changed. Returns when the app does not
    run.

        refuse_while_running()    # returns, or raises Cancelled("Quit the oMLX app first …")
    """
    if running():
        raise Cancelled(QUIT_FIRST)
