"""stack docs overview — generate the family wiki entry pages (experimental).

Walks the memory vault summaries and composes the pages a family lands
on in the wiki:

    stack docs overview              the household home page (index.md)
    stack docs overview --members    the home page + one page per member
    stack docs overview --member X   just member X's page

Output goes to stdout by default; `--write` splices each generated page
into the regenerate region of the corresponding `index.md` on Forgejo,
and the wiki picks it up within seconds.

This is a v1 hidden command for the household to try on their own vault
before we automate it. No incremental updates, no scheduling -- just a
manual run.
"""

HELP = "Generate the family wiki entry pages (experimental)"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import dispatch  # noqa: E402


def run(args, stacklet, config):
    if not config["is_healthy"]():
        return {"error": "Docs is not running — start it with 'stack up docs'"}
    argv = sys.argv[3:]
    return dispatch("overview", *argv)
