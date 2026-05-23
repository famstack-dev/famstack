"""stack docs overview — generate the family about page (experimental).

Walks the memory vault summaries and composes a single about page
with fixed sections (Name, Address, Members, Broader Family, Home,
Real Estate, Vehicles, Insurance). Output goes to stdout by default;
`--write` lands it at `<vault>/<shared_bucket>/about.md`.

This is a v1 hidden command for the household to try on their own
vault before we automate it. No incremental updates, no scheduling --
just a manual `stack docs overview` run.
"""

HELP = "Generate the family about page (experimental)"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import dispatch  # noqa: E402


def run(args, stacklet, config):
    if not config["is_healthy"]():
        return {"error": "Docs is not running — start it with 'stack up docs'"}
    argv = sys.argv[3:]
    return dispatch("overview", *argv)
