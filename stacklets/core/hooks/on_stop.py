"""Stop the famstack API server and take it out of the login items.

on_start writes the LaunchAgent with RunAtLoad on every up, so the file
only has to exist while core is up. Left in place, launchd would start
the API again at the next login while core reports down, and after a
destroy it would keep respawning a wrapper that no longer exists.
Removing it here covers down, destroy and uninstall, which all run
on_stop.

launchd is asked first, by the label, as the AI's stop does: the job it
runs decides, not the agent file, which may be gone or unreadable while
the host API still runs. The file decides only when launchd has no job.

Only the agent this instance wrote is touched. Another checkout or data
dir writes the same label with its own wrapper, and that one is left
loaded and in place: launchd names the program a job runs, and the job is
this instance's when that is its wrapper.
"""

import plistlib
from pathlib import Path
from xml.parsers.expat import ExpatError

from stack import launchd

PLIST_LABEL = "dev.famstack.api"


def run(ctx):
    """Stop this instance's host API and remove its agent file. Raises
    `RuntimeError` when launchd cannot be asked or does not let it go."""
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
    # The wrapper on_start points the agent at.
    wrapper = Path(ctx.stack.data) / "core" / "famstack-api"
    said = launchd.listed(ctx.shell, PLIST_LABEL)
    if said is not None:
        if f'"{wrapper}"' not in said:
            return  # another instance's host API
        # Returns once launchd let the job go, so a `stack restart core`
        # finds the port and the label free for its start.
        launchd.stop(ctx.shell, PLIST_LABEL)
        plist_path.unlink(missing_ok=True)
        ctx.step("famstack API stopped")
    elif _names(plist_path, wrapper):
        # Nothing runs, but the file would start the host API at the
        # next login.
        plist_path.unlink()


def _names(plist_path: Path, wrapper: Path) -> bool:
    """True when the agent at plist_path starts this instance's wrapper."""
    try:
        with plist_path.open("rb") as f:
            plist = plistlib.load(f)
    except (OSError, ValueError, ExpatError):
        # Missing or unreadable: nothing of ours to remove.
        return False
    return plist.get("ProgramArguments") == [str(wrapper)]
