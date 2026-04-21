"""stack docs reformat <id> — reformat a doc's OCR body and apply to Paperless.

Runs the archivist's OCR-to-markdown prompt on the doc's current content
and replaces the Paperless body with the clean markdown.

Use `--dry-run` (or `--dry`) to preview without writes. Use `--raw` for
pipe-friendly raw markdown output (implies dry).

Usage:
    stack docs reformat <id> [--dry | --dry-run] [--raw]

Examples:
    stack docs reformat 42                  # reformat + apply
    stack docs reformat 42 --dry-run        # preview, no writes
    stack docs reformat 42 --raw > doc.md   # raw markdown, pipeable
"""

HELP = "Reformat a document's OCR body and apply to Paperless"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import dispatch  # noqa: E402


def run(args, stacklet, config):
    if not config["is_healthy"]():
        return {"error": "Docs is not running — start it with 'stack up docs'"}
    argv = sys.argv[3:]
    return dispatch("reformat", *argv)
