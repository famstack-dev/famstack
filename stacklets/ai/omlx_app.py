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

One check, the app's processes by their executable: `ps -axo comm=`
lists the full path each process was started from, spaces included, and
`grep -F` looks for one inside an `oMLX.app` bundle (`APP_BUNDLE`). That
finds the app and a server it starts from its bundle, wherever the app is
installed (`/Volumes/Macintosh HD 2/Applications`, a folder with a
space in its name), and never famstack's oMLX, which Homebrew installs
outside any app bundle. A process that only names a path in the bundle
among its arguments (an editor, a `tail` of a file in it, a test's
stand-in) is not the app: a match anywhere in the command line made
famstack's oMLX step aside for such a process, and stay down. The guard
of famstack's oMLX (`omlx_service.guard`) runs the same check
(`app_check`). No port is probed: a port says that something answers,
not what it is.

What it does not do: quit the app, or look at an oMLX on another machine
(that one has its own graphics chip). The callers are the places that
install, start or restart famstack's oMLX: `prepare.py`,
`hooks/on_start.py` (every `stack up ai`, `stack ai switch managed`
included) and `cli/key.py` (`stack ai key new`, which returns the message
as its error). launchd starts famstack's oMLX at login without asking
this module.
"""

from __future__ import annotations

import shlex
import subprocess

from stack.hooks import Cancelled

__all__ = ["APP_BUNDLE", "PROCESS_COMMAND", "app_check", "QUIT_FIRST", "running",
           "refuse_while_running"]

# Part of the executable path of every process the app runs: the app and a
# server it starts from its bundle.
APP_BUNDLE = "/oMLX.app/Contents/"


def app_check(bundle: str = APP_BUNDLE, *, ps: str = "ps", grep: str = "grep") -> str:
    """The shell line that exits 0 while a process runs whose executable
    lies in `bundle` (a fixed text, no pattern). `ps` and `grep` are found
    on PATH unless named by full path: the guard names them, because
    launchd gives it only the PATH its agent writes; a test names its own
    bundle, so it sees only its stand-in.

        app_check()
        # "ps -axo comm= | grep -qF -- /oMLX.app/Contents/"
        app_check(ps="/bin/ps", grep="/usr/bin/grep")    # the guard's
    """
    return f"{ps} -axo comm= | {grep} -qF -- {shlex.quote(bundle)}"


# What finds the app's processes; exits 0 when one runs. `ps` is looked up
# on PATH at each call, so a test can stand in for it, also in a `stack`
# the test starts.
PROCESS_COMMAND = ("/bin/sh", "-c", app_check())
# `ps` answers at once; the limit only keeps a hung one from holding
# the run.
PROCESS_SECONDS = 10

QUIT_FIRST = ("Quit the oMLX app first (its icon in the menu bar, then Quit), then run this "
              "again. Two AI servers on one Mac's graphics chip can crash the Mac. "
              "Nothing changed.")


def running() -> bool:
    """Whether a process of the oMLX app runs on this Mac. False when
    `PROCESS_COMMAND` finds none or cannot be run. Reads only.

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
