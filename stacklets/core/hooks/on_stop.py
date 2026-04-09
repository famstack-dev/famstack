"""Stop the famstack API socket server."""

from pathlib import Path

PLIST_LABEL = "dev.famstack.api"


def run(ctx):
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
    if plist_path.exists():
        try:
            ctx.shell(f'launchctl unload "{plist_path}"')
        except RuntimeError:
            pass
