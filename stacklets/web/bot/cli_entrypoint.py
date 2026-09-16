"""Web CLI dispatcher — executed inside stack-core-bot-runner.

Reasonable question on reading this: a chat completion is one POST with
a JSON body, so why route it through a container at all instead of
calling the endpoint from the host with urllib? It has been asked, and
answered the hard way, so the answer lives here.

**One LLM client, not two.** `stack.ai.client` carries knowledge that
was expensive to acquire and is invisible until it is missing. The
sharpest example is `chat_template_kwargs: {enable_thinking: False}`:
without it a Qwen3 writes its deliberation into `content` and never
reaches the answer, and every other way of switching that off
(`reasoning_effort`, a top-level `enable_thinking`, a `reasoning`
object, a `/no_think` suffix) was verified against oMLX and had no
effect. A hand-rolled urllib version of this file was written, and lost
exactly that knob, and produced "Here's a thinking process: 1. Analyze
User Question" where an answer should have been. Timeouts tuned to
measured prefill rates and the typed error mapping are the same kind of
debt waiting to be re-incurred.

**Hostile input belongs in a container.** Search snippets are
attacker-influenced text: a page that ranks can say whatever it likes,
and that text goes into a prompt here and out to whatever called us.
The completion itself holds no tools, so the blast radius is content
rather than execution — but this is the seam where hostile bytes meet
our code, and the host is where the SSH keys and the vault live. A
family server should not be parsing the internet as the person who owns
the machine.

Same arrangement as `stack docs` and `stack memory capture`; the
mechanism itself is `stack.bot_runner`, lifted there when the pattern
found its second user.

`stack web fetch` runs here too, and for a sharper reason than ask:
extraction needs trafilatura and lxml, which the host does not have.
Before this it silently answered "empty" for every page that was not
served by tier 0 or tier 1, because a missing extractor and an absent
article were indistinguishable from the gate's side.

There is a second reason that survives even after the dependency is
present. A diagnostic command has to reproduce production. The archivist
reads pages with this code, inside this container, against these library
versions; a CLI that reads them with different ones answers a question
nobody asked. Measured on one chefkoch listing page, two trafilatura
versions returned 106 and 433 characters for identical bytes, which
lands either side of the gate's floor.

Commands:
    ask   "<question>" [--json] [--sources N]
    fetch <url> [--json] [--timeout N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

sys.path.insert(0, "/stacklets/web/cli")  # search()
sys.path.insert(0, "/app")  # stack.* — the framework is mounted there

from search import DEFAULT_COUNT, search  # noqa: E402
from stack.ai.client import LLM, LLMError  # noqa: E402
from stack.web import fetch_url  # noqa: E402
from stack.web.ask import build_prompt, render_sources, sources_from  # noqa: E402
from stack.web.fetch import urllib_transport  # noqa: E402

# Container-side address. The published host port is not reachable from
# in here; both containers sit on the external `stack` network.
SEARCH_URL = "http://stack-web-search:8080/search"


async def _ask(question: str, count: int, as_json: bool) -> int:
    try:
        results = search(question, count=count, base_url=SEARCH_URL)
    except OSError:
        print("Ask needs the web stacklet running. Run `stack up web`.",
              file=sys.stderr)
        return 1

    sources = sources_from(results, limit=count)
    if not sources:
        print(f'Search returned nothing for "{question}".', file=sys.stderr)
        return 1

    llm = LLM.from_env(namespace="web")
    try:
        # Capped because the size of a valid answer is known in advance:
        # a few sentences plus citations. Without a cap, a model that
        # enters a repetition loop generates until the client timeout.
        answer = (await llm.complete(
            "ask", build_prompt(question, sources),
            temperature=0.0, max_tokens=600, timeout=180.0,
        )).strip()
    except LLMError as e:
        # The search worked; only the summarising failed. The links are
        # still worth printing -- the family can read them itself.
        print(f"\n  Couldn't reach the model ({e}). What the search found:\n")
        print(render_sources(sources))
        print()
        return 1
    finally:
        await llm.aclose()

    if as_json:
        print(json.dumps({
            "question": question,
            "answer": answer,
            "sources": [{"n": s.n, "title": s.title, "url": s.url} for s in sources],
        }, indent=2, ensure_ascii=False))
        return 0

    print()
    print(answer)
    print()
    print("  Sources")
    print(render_sources(sources))
    print()
    return 0


async def _fetch(url: str, timeout: int, as_json: bool) -> int:
    outcome = await fetch_url(url, transport=urllib_transport(timeout=timeout))

    if as_json:
        print(json.dumps({
            "url": outcome.url,
            "verdict": outcome.verdict.name,
            "detail": outcome.verdict.detail,
            "tier": outcome.tier,
            "profile": outcome.profile,
            "title": outcome.content.title_hint if outcome.content else None,
            "text": outcome.content.text if outcome.content else None,
        }, indent=2, ensure_ascii=False))
        return 0 if outcome.ok else 1

    # Two lines either way: what we ended up reading, and how we got
    # there. A refusal is the same shape with no body under it.
    print()
    print(f"  {outcome.content.title_hint if outcome.content else outcome.url}")
    if outcome.ok:
        print(f"  tier {outcome.tier} ({outcome.profile}) — {outcome.verdict.detail}")
        print()
        print(outcome.content.text)
    else:
        print(f"  {outcome.verdict.name} — {outcome.verdict.detail}")
    print()
    return 0 if outcome.ok else 1


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("ask", "fetch"):
        print(__doc__.strip(), file=sys.stderr)
        return 2

    if argv[0] == "fetch":
        parser = argparse.ArgumentParser(prog="stack web fetch", add_help=False)
        parser.add_argument("url", nargs="?")
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--timeout", type=int, default=30)
        opts = parser.parse_args(argv[1:])
        if not opts.url:
            print('usage: stack web fetch <url>', file=sys.stderr)
            return 2
        return asyncio.run(_fetch(opts.url, opts.timeout, opts.json))

    parser = argparse.ArgumentParser(prog="stack web ask", add_help=False)
    parser.add_argument("question", nargs="*")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--sources", type=int, default=DEFAULT_COUNT)
    opts = parser.parse_args(argv[1:])

    question = " ".join(opts.question).strip()
    if not question:
        print("usage: stack web ask \"<question>\"", file=sys.stderr)
        return 2

    return asyncio.run(_ask(question, opts.sources, opts.json))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
