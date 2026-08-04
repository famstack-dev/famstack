"""Memory CLI dispatcher — executed inside stack-core-bot-runner.

The host-side `stack memory wiki` dispatcher `docker exec`s into the
bot-runner container and invokes this entry point. The container has
the framework's LLM client, aiohttp, frontmatter, and the rendered AI
env vars — so the host CLI stays stdlib-only while the LLM-using
command runs against the same model the archivist does.

Same pattern as `stacklets/docs/bot/cli_entrypoint.py`; both stacklets
re-use the bot-runner as their tools runtime.

Commands:
    rewrite <question>
        Print the search keywords a question should be looked up by,
        one per line. `stack memory search --nl` is the caller: it
        does the searching on the host and only comes here for the
        words. Exit 1 means no keywords, which the host treats as
        "search it literally" rather than as a failure.

    wiki [--home] [--member <slug>]... [--topic <slug>]... [--dry-run]
        Regenerate the family wiki's entry pages. Apply by default;
        `--dry-run` previews to stdout. Bare invocation regenerates
        home + every member + every topic; selection flags repeat and
        combine to cover exactly the pages a filing burst touched (the
        curator's incremental path). See `cli/wiki.py` for the full
        prompt-and-splice contract.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # cli/
sys.path.insert(0, "/app")  # stack.ai.client, stack.forgejo, stack.prompt

from stack.ai.client import LLM, LLMUnavailableError

from cli import rewrite, wiki


_HANDLERS = {
    "rewrite": rewrite.run,
    "wiki": wiki.run,
}


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


async def main(argv: list[str]) -> int:
    if not argv:
        _usage()
        return 2

    cmd, *rest = argv
    fn = _HANDLERS.get(cmd)
    if not fn:
        _err(f"Unknown command: {cmd}")
        _usage()
        return 2

    try:
        llm = LLM.from_env(namespace="memory-wiki")
    except LLMUnavailableError as e:
        _err(str(e))
        return 1

    try:
        return await fn(llm, rest)
    finally:
        await llm.aclose()


def _usage() -> None:
    _err(__doc__.rstrip())


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
