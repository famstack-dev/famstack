"""stack docs reprocess <id> [<id>...] — re-run the archivist pipeline.

Fetches the filed doc, classifies it again, applies topic/person/type/
correspondent/date to Paperless, optionally reformats the OCR body, and
optionally updates the Forgejo mirror.

`--reformat` and `--mirror` default to whatever the archivist's bot.toml
[settings] says, so the CLI behaves the same way the bot does on a new
upload. Pass the `--no-*` form to opt out for a single run.

Usage:
    stack docs reprocess <id> [<id>...] \\
        [--reformat | --no-reformat] \\
        [--mirror   | --no-mirror]   \\
        [--dry-run | --dry]

Examples:
    stack docs reprocess 42                 # respect bot.toml, apply
    stack docs reprocess 42 43 44           # batch, one at a time
    stack docs reprocess 42 --dry-run       # plan only, no writes
    stack docs reprocess 42 --no-reformat   # skip the reformat LLM call
"""

HELP = "Re-run the archivist pipeline on filed documents (apply + mirror)"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import dispatch  # noqa: E402


def run(args, stacklet, config):
    if not config["is_healthy"]():
        return {"error": "Docs is not running — start it with 'stack up docs'"}
    argv = sys.argv[3:]
    return dispatch("reprocess", *argv)
