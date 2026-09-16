"""stack web fetch <url> — read a web page the way the archivist does.

Literally the way the archivist does: same code, same container, same
library versions. That is the whole value of the command. When a link
files badly the question is "what did we actually get back", and an
answer produced by a different extractor on a different machine is not
an answer to it.

    stack web fetch https://www.essen-und-trinken.de/rezepte/48816-...
      Griechischer Salat Rezept
      tier 1 (default) — the page published its own structured data

      **Servings:** 4 · **Total time:** 35 min
      ## Ingredients
      - 500 g rote und gelbe Paprika
      ...

A page we cannot read says so, and says which obstacle it was:

    stack web fetch https://www.decathlon.de/
      https://www.decathlon.de/
      challenge — bot protection served 'just a moment...' instead of the page

A refusal is a successful read of a blocked page, not a command
failure, so it is `--json` that a script branches on: the `verdict`
field carries which of the refusals it was, alongside the tier, the
profile, the title and the body.

Runs in the bot-runner. It used to run on the host, which was wrong
twice over: the host has no trafilatura, so every page not served by
tier 0 or tier 1 came back "empty" whether or not it had content, and
even once that is fixed a host-side reader would be parsing hostile
HTML as the user who owns the machine.
"""

HELP = "Read a web page and print it as Markdown"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from stack.bot_runner import dispatch  # noqa: E402

_ENTRYPOINT = "/stacklets/web/bot/cli_entrypoint.py"


def run(args, stacklet, config):
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return {"ok": True}
    return dispatch(_ENTRYPOINT, "fetch", *args)
