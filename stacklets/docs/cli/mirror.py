"""stack docs mirror <id> [<id>...] — push existing docs to the Forgejo mirror.

Useful for backfilling the mirror after enabling `mirror_to_git` in
bot.toml: docs already in Paperless weren't mirrored at upload time, and
this command walks each requested id, publishing the current Paperless
state (title, tags, correspondent, content) to Forgejo. No LLM call —
classification stays exactly as it is in Paperless.

Fails fast when `mirror_to_git = false` in bot.toml: flip it, then run.

Usage:
    stack docs mirror <id> [<id>...] [--dry-run]

Examples:
    stack docs mirror 42                     # publish doc #42
    stack docs mirror 42 43 44 --dry-run     # plan only, no commits
"""

HELP = "Publish existing documents to the Forgejo mirror (no LLM)"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import dispatch  # noqa: E402


def run(args, stacklet, config):
    if not config["is_healthy"]():
        return {"error": "Docs is not running — start it with 'stack up docs'"}
    argv = sys.argv[3:]
    return dispatch("mirror", *argv)
