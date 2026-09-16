"""stack web fetch <url> — read a web page the way the archivist does.

Host-native. Same code path the bot takes for a pasted link, with
nothing running: canonicalize the URL, try the page's own structured
data, fall back to HTTP plus extraction, and put the result through the
quality gate. No container, no model, no network hop to a service.

That is the point of the command. When a link files badly, the question
is always "what did we actually get back", and answering it should not
require the stack to be up or a bot to be restarted.

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
failure, so the exit code stays 0. The branch point for a script is
`--json`, whose `verdict` field carries which of the five refusals it
was — more than an exit code could say — alongside the tier, the
profile, the title and the body.
"""

HELP = "Read a web page and print it as Markdown"

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from stack.web import fetch_url  # noqa: E402
from stack.web.fetch import urllib_transport  # noqa: E402


def run(args, stacklet, config):
    parser = argparse.ArgumentParser(prog="stack web fetch", add_help=False)
    parser.add_argument("url", nargs="?")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("-h", "--help", action="store_true")
    opts = parser.parse_args(args)

    if opts.help or not opts.url:
        print(__doc__.strip())
        return {"ok": True}

    outcome = asyncio.run(fetch_url(
        opts.url, transport=urllib_transport(timeout=opts.timeout),
    ))

    if opts.json:
        print(json.dumps({
            "url": outcome.url,
            "verdict": outcome.verdict.name,
            "detail": outcome.verdict.detail,
            "tier": outcome.tier,
            "profile": outcome.profile,
            "title": outcome.content.title_hint if outcome.content else None,
            "text": outcome.content.text if outcome.content else None,
        }, indent=2, ensure_ascii=False))
        return {"ok": outcome.ok}

    # The header is the same two lines either way -- what we ended up
    # reading, and how we got there. A refusal is not an error report,
    # it is the same shape with no body under it.
    print()
    print(f"  {outcome.content.title_hint if outcome.content else outcome.url}")
    if outcome.ok:
        print(f"  tier {outcome.tier} ({outcome.profile}) — {outcome.verdict.detail}")
        print()
        print(outcome.content.text)
    else:
        print(f"  {outcome.verdict.name} — {outcome.verdict.detail}")
    print()
    return {"ok": outcome.ok}
