"""stack docs show <id> — inspect a document's current Paperless state.

Read-only: title, date, type, correspondent, tags, and a content preview.
Useful for sanity-checking a doc before reprocess, and for comparing
before/after when tuning prompts or seeding new taxonomy.

Usage:
    stack docs show <id> [--content]    --content prints the full body.
"""

HELP = "Show a document's current Paperless state"

import sys

from ._common import dispatch


def run(args, stacklet, config):
    if not config["is_healthy"]():
        return {"error": "Docs is not running — start it with 'stack up docs'"}
    argv = sys.argv[3:]  # skip 'stack', 'docs', 'show'
    return dispatch("show", *argv)
