"""stack web ask <question> — a plain question, a short answer, its sources.

One search, the top handful of snippets, one model call. No second
query, no fetching the pages, no tool loop deciding what to read next.

    stack web ask "does immich support raw files"
      Yes. Immich stores the original RAW file untouched [2][4];
      PhotoPrism reads RAW but converts for display [4].

      Sources
        [2] Immich vs PhotoPrism 2026
            https://example.com/immich-vs-photoprism

That shape is a judgement about what a local model can actually do, not
a shortcut. The best measured browser agents finish about a third of
ordinary web tasks, and multi-step tool use is where small models fall
off hardest. One call over snippets a search engine already ranked has
none of those failure modes: it either finds the answer in the text it
was handed or it says it did not.

The sources print whether or not an answer was found. "I could not
answer this, here is what I looked at" lets the family judge for
themselves; a bare refusal does not.

    stack web ask "..." --json         for an agent
    stack web ask "..." --sources 4    fewer, shorter

Needs the web stacklet up for search, and core up for the model: the
work runs inside the bot-runner, because the host CLI is stdlib-only
and the LLM client is not. `stack web fetch` has no such dependency and
still works with nothing running.
"""

HELP = "Ask a question and get an answer with sources"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from stack.bot_runner import dispatch  # noqa: E402

_ENTRYPOINT = "/stacklets/web/bot/cli_entrypoint.py"


def run(args, stacklet, config):
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return {"ok": True}
    return dispatch(_ENTRYPOINT, "ask", *args)
