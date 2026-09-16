"""Web CLI dispatcher — executed inside stack-core-bot-runner.

`stack web ask` needs two things the host cannot give it: the LLM
client (which wraps the OpenAI SDK) and the rendered AI environment.
The host-side `./stack` is stdlib-only by design, so rather than
install the SDK on the host or reimplement the client in urllib, the
host command `docker exec`s into the bot-runner and runs this.

Same arrangement as `stack docs` and `stack memory capture`; the
mechanism itself is `stack.bot_runner`.

`stack web fetch` and `stack web search` deliberately do *not* come
through here. Fetch is stdlib all the way down and must keep working
with nothing running at all, and search is a single HTTP GET. Only the
model call needs the container.

Commands:
    ask "<question>" [--json] [--sources N]
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
from stack.web.ask import build_prompt, render_sources, sources_from  # noqa: E402

# Container-side address. The published host port is not reachable from
# in here; both containers sit on the external `stack` network.
SEARCH_URL = "http://stack-web-search:8080/search"


async def _ask(question: str, count: int, as_json: bool) -> int:
    try:
        results = search(question, count=count, base_url=SEARCH_URL)
    except OSError:
        print("Search is not reachable. Is the web stacklet up?", file=sys.stderr)
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


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "ask":
        print(__doc__.strip(), file=sys.stderr)
        return 2

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
