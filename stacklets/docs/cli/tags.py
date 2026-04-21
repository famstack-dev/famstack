"""stack docs tags — inspect and clean up Paperless tags + types.

Subcommands:
    stack docs tags                              List tags (default view).
    stack docs tags --types                      List document types instead.
    stack docs tags --owner=N                    Filter by owner id.
    stack docs tags --used | --unused            Filter by document_count.
    stack docs tags merge <from> <to> [--type]   Retag every doc, then delete
                                                 <from>. With --type, merges
                                                 document_types instead.
    stack docs tags prune --lang <de|en>         Delete seeded entries from
                                                 that language section that
                                                 have zero documents.

All write-capable subcommands accept --dry / --dry-run for a preview.
"""

HELP = "Inspect and clean up Paperless tags and types"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import dispatch  # noqa: E402


def run(args, stacklet, config):
    if not config["is_healthy"]():
        return {"error": "Docs is not running — start it with 'stack up docs'"}
    argv = sys.argv[3:]  # skip 'stack', 'docs', 'tags'
    return dispatch("tags", *argv)
