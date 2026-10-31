"""stack core web: the stack's own web reading.

    stack core web fetch <url>              the page as Markdown, as the stack reads it
    stack core web fetch <url> --json       verdict, tier, profile, title and text
    stack core web fetch <url> --timeout 10

`fetch` reads one page the way the stack reads every link: the fetch
ladder in `stack.web` (canonical URL, the page's structured data, plain
HTTP and extraction, and the quality gate), which the archivist also
files a pasted link with. When a link was filed badly, this shows what
the stack got from that page.
Only public http and https addresses are read, as the archivist reads
them: the bot runner sees the family's data and sits on the stack's
network, so a `file:` address or a page in the home network is refused
like a blocked page.

    stack core web fetch https://example.com/recipes/greek-salad
      Greek salad
      tier 1 (default): the page published its own structured data

      **Servings:** 4 · **Total time:** 35 min
      ...

A page the stack cannot read says which obstacle stopped it. That is a
read of a blocked page, not a failure of the command: it exits 0, and
`verdict` in `--json` says which refusal it was.

    stack core web fetch https://example.com/shop
      https://example.com/shop
      challenge: bot protection served 'just a moment...' instead of the page

The read runs in core's bot runner (`python -m stack.web`), which has the
extractor and keeps a hostile page's HTML off the host, so core must be up.
"""

HELP = "Read a web page as the stack reads a link: fetch"

import argparse
import json
import subprocess

from stack import bot_runner

USAGE = "stack core web fetch <url> [--timeout <seconds>]"
# Room for the bot runner to start the module on top of the page's own timeout.
_START_S = 30


def run(args, stacklet, config):
    match list(args):
        case ["fetch", *rest]:
            return _fetch(rest)
        case []:
            print(f"  {USAGE}")
            return {}
    return {"error": f"Unknown action '{args[0]}'. Usage: {USAGE}"}


def _fetch(args: list[str]) -> dict:
    parser = argparse.ArgumentParser(prog="stack core web fetch", add_help=False)
    parser.add_argument("url", nargs="?")
    parser.add_argument("--timeout", type=int, default=30)
    opts = parser.parse_args(args)
    if not opts.url:
        return {"error": f"Usage: {USAGE}"}
    if not bot_runner.bot_runner_running():
        return {"error": "Reading a page needs core: start it with 'stack up core'."}

    request = json.dumps({"url": opts.url, "timeout": opts.timeout})
    try:
        done = bot_runner.run_module("stack.web", request, timeout=opts.timeout + _START_S)
    except subprocess.TimeoutExpired:
        return {"error": f"Reading {opts.url} took longer than {opts.timeout + _START_S} s"}
    if done.returncode != 0:
        last = (done.stderr.strip().splitlines() or ["no answer"])[-1]
        return {"error": f"Reading {opts.url} failed: {last}"}
    answer = json.loads(done.stdout.strip().splitlines()[-1])
    print(_page(answer))
    return answer


def _page(answer: dict) -> str:
    """What was read and how, then the text, as people read it."""
    head = f"\n  {answer['title'] or answer['url']}"
    if answer["verdict"] != "ok":
        return f"{head}\n  {answer['verdict']}: {answer['detail']}\n"
    how = f"  tier {answer['tier']} ({answer['profile']}): {answer['detail']}"
    return f"{head}\n{how}\n\n{answer['text']}\n"
