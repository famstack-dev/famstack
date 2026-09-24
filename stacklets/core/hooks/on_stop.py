"""Stop the famstack API server and take it out of the login items.

on_start writes the LaunchAgent with RunAtLoad on every up, so the file
only has to exist while core is up. Left in place, launchd would start
the API again at the next login while core reports down, and after a
destroy it would keep respawning a wrapper that no longer exists.
Removing it here covers down, destroy and uninstall, which all run
on_stop.

Only the agent this instance wrote is touched. Another checkout or data
dir writes the same label with its own wrapper, and that one is left
loaded and in place.
"""

import plistlib
from pathlib import Path
from xml.parsers.expat import ExpatError

PLIST_LABEL = "dev.famstack.api"


def run(ctx):
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
    # The wrapper on_start points the agent at.
    wrapper = Path(ctx.stack.data) / "core" / "famstack-api"
    if not _runs(plist_path, wrapper):
        return

    try:
        ctx.shell(f'launchctl unload "{plist_path}"')
    except RuntimeError:
        pass
    plist_path.unlink()
    ctx.step("famstack API stopped")


def _runs(plist_path: Path, wrapper: Path) -> bool:
    """True when the agent at plist_path starts this instance's wrapper."""
    try:
        with plist_path.open("rb") as f:
            plist = plistlib.load(f)
    except (OSError, ValueError, ExpatError):
        # Missing or unreadable: nothing of ours to remove.
        return False
    return plist.get("ProgramArguments") == [str(wrapper)]
