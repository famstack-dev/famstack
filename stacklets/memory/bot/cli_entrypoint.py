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

    diary pages [--fast] [--month <YYYY-MM>]...
        Compile the diary pages again from the diary records on file,
        without reading the room: what the curator runs when a diary
        card was pushed. `--fast` asks no model.

    diary [<room>] [--burst-window <seconds>] [--dry-run] [--force] [--retranscribe] [--limit <n>] [--preview <n>] [--rebuild]
        Compile the memories room into the family diary. Walks the
        room's full history, transcribes every recording, recovers the
        date each one was made, and publishes month pages under the
        shared bucket. The curator runs it on the nightly sweep.

        Always a full pass, never an append: a reply or an edit
        arriving tonight can belong to an entry from years back. It
        stays cheap because transcripts, readings, and month summaries
        are all kept against the thing they describe, so only what is
        new costs anything. A card is written again only when its
        entry changed; `--rebuild` writes every card under the current
        prompts. `--force` ignores every cache and reads everything
        again, for when the model has improved. `--limit`
        and `--preview` read part of the room and write nothing;
        `--preview` prints a JSON report of the newest entries' cards.

    lists [--dry-run]
        Move the items ticked before today from every list into its
        archive, a file per month under `lists/archive/<slug>/`. The
        curator runs it on the nightly sweep. No model involved.

    wiki [--home] [--member <slug>]... [--topic <slug>]... [--correspondent <name>]... [--fast] [--dry-run]
        Regenerate the family wiki's entry pages. Apply by default;
        `--dry-run` previews to stdout. Bare invocation regenerates
        home + every member + every topic; selection flags repeat and
        combine to cover exactly the pages a filing burst touched (the
        curator's incremental path). `--fast` asks no model: the pages
        show the cards as they are, in the model's last wording. See
        `cli/wiki.py` for the full prompt-and-splice contract.

A command run with `--fast` needs no model, so it runs without one.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app")  # stack.ai.client, stack.http_json, stack.prompt

# This file runs as a script (the curator and the bot runner start it), so
# it says once where the stacklets are; memory's commands are then modules
# of the package `stacklets.memory` (`stack.stacklet_code`).
from stack.stacklet_code import import_stacklets  # noqa: E402

import_stacklets([Path(__file__).resolve().parents[2]])

from loguru import logger  # noqa: E402

from stack.ai.client import LLM, LLMUnavailableError  # noqa: E402

from stacklets.memory.bot.cli import answer, diary, lists, rewrite, wiki  # noqa: E402


_HANDLERS = {
    "answer": answer.run,
    "diary": diary.run,
    "lists": lists.run,
    "rewrite": rewrite.run,
    "wiki": wiki.run,
}


# What a command prints is read by people: `stack memory wiki update` and
# the curator echo it. Debug lines (what each model call cost,
# `call_costs`) stay out unless `LOG_LEVEL` asks for them, as in the bot
# runner. The format stays loguru's default.
def _log_level() -> str:
    """`LOG_LEVEL` in any case, INFO when unset; an unknown one is named once
    and INFO is used, so a typo never stops every memory command."""
    asked = os.environ.get("LOG_LEVEL", "").strip()
    try:
        return logger.level(asked.upper()).name if asked else "INFO"
    except ValueError:
        print(f"LOG_LEVEL={asked!r} is not a log level, using INFO", file=sys.stderr)
        return "INFO"


logger.remove()
logger.add(sys.stderr, level=_log_level())


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

    if "--fast" in rest:
        return await fn(None, rest)
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
    _err((__doc__ or "").rstrip())


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main(sys.argv[1:])))
    except KeyboardInterrupt:
        # Exit 130 (128 + SIGINT) without the asyncio traceback.
        # Completed transcripts and readings are already on disk.
        _err("\ninterrupted; completed work is cached")
        sys.exit(130)
