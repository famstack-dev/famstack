"""stack docs mirror <id> [<id>...] — push existing docs to the memory vault.

Useful for backfilling existing Paperless docs into a freshly-installed
memory vault. This command walks each requested id, publishing the
current Paperless state (title, tags, correspondent, content) into
`family/memory/<shared_bucket>/documents/`. No LLM call — classification
stays exactly as it is in Paperless.

Fails fast when the `code` stacklet env (CODE_URL, admin creds) is
unavailable: bring up `code` and re-run.

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
