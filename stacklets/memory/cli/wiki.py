"""stack memory wiki — regenerate the family wiki's entry pages.

Walks the memory vault, asks the LLM to compose the household home
page and a personal page for every household member, and publishes
the result to the memory repo on Forgejo. The wiki container picks
the change up within seconds.

Apply by default; `--dry-run` is the opt-in preview that streams the
generated pages to stdout without writing anywhere.

    stack memory wiki                 # home + every member page (apply)
    stack memory wiki --home          # just the household home page
    stack memory wiki --member homer  # just one member's page
    stack memory wiki --dry-run       # preview, no writes

Updates use a splice contract: the LLM-generated body lives inside
`<!-- begin: generated --> ... <!-- end: generated -->` markers in
each page. Everything outside the markers — a welcome line, custom
frontmatter, hand-written notes — survives every regen.

The command runs inside `stack-core-bot-runner` (the bot-runner has
the LLM client and the rendered env); the host wrapper is a thin
docker-exec.
"""

HELP = "Regenerate the family wiki's home and member pages"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import dispatch  # noqa: E402


def run(args, stacklet, config):
    argv = sys.argv[3:]
    return dispatch("wiki", *argv)
