"""Docs CLI dispatcher — executed inside stack-core-bot-runner.

The host-side `stack docs <cmd>` dispatchers `docker exec` into the
bot-runner container and invoke this entry point. The container already
has aiohttp, loguru, yaml, and the rendered Paperless / AI env vars —
so the host CLI stays stdlib-only while the pipeline logic is shared
verbatim with the archivist bot.

The pattern is deliberately reusable: any stacklet CLI that needs
non-stdlib deps can grow its own `cli_entrypoint.py` here and a thin
dispatcher on the host. Beats either "install aiohttp on the host" or
"duplicate HTTP code in urllib".

Commands:
    show <id> [--content]           pretty-print Paperless state
    classify <id> [--dry] [--json]  classify + apply (--dry previews,
                                    --json dumps raw LLM output)
    reformat <id> [--dry] [--raw]   reformat OCR + apply content
                                    (--dry previews, --raw dumps markdown)
    reprocess <id> [<id>...]        full pipeline (classify + reformat
                                    + mirror), respects bot.toml [settings]
                                    flags: --[no-]reformat --[no-]mirror --dry
    mirror <id> [<id>...] [--dry]   push current Paperless state to the
                                    Forgejo mirror (no LLM). Useful for
                                    backfilling after enabling mirror_to_git.
    tags [--types] [--used|--unused] [--owner=N]
                                    list tags or document_types
    tags merge <from> <to> [--type] [--dry]    retag docs, drop source
    tags prune --lang <de|en> [--dry]          delete unused seeded entries
    tags delete <name> [--type] [--dry]        delete (refuses if in use)

Every write command accepts `--dry` and `--dry-run` interchangeably.

Each subcommand lives in cli/<name>.py as a single `async def run(...)`;
this file just routes on argv[0] and provides the shared aiohttp session
+ Paperless/Classifier instances.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # pipeline, matching, cli/
sys.path.insert(0, "/app")  # stack.resolve_model, stack.prompt

import aiohttp

from pipeline import Classifier, PaperlessAPI

from cli import classify, mirror, reformat, reprocess, show, tags
from cli._shared import err


_HANDLERS = {
    "show": show.run,
    "classify": classify.run,
    "reformat": reformat.run,
    "reprocess": reprocess.run,
    "mirror": mirror.run,
    "tags": tags.run,
}


async def main(argv: list[str]) -> int:
    if not argv:
        _usage()
        return 2

    cmd, *rest = argv
    fn = _HANDLERS.get(cmd)
    if not fn:
        err(f"Unknown command: {cmd}")
        _usage()
        return 2

    paperless_url = os.environ.get("PAPERLESS_URL", "")
    paperless_token = os.environ.get("PAPERLESS_TOKEN", "")
    if not paperless_url or not paperless_token:
        err("PAPERLESS_URL / PAPERLESS_TOKEN not set — bot-runner env missing docs creds.")
        return 1

    async with aiohttp.ClientSession() as http:
        paperless = PaperlessAPI(http, paperless_url, paperless_token)
        classifier = Classifier(
            http,
            os.environ.get("OPENAI_URL", ""),
            os.environ.get("OPENAI_KEY", ""),
        )
        return await fn(paperless, classifier, rest)


def _usage() -> None:
    err(__doc__.rstrip())


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
