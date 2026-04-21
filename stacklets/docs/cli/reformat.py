"""stack docs reformat <id> — dry-run reformatter against a filed document.

Runs the archivist's OCR-to-markdown prompt and prints the result. Paperless
is not touched, mirror is not updated. Useful for previewing what the
reformat pass would do and for debugging reformats that seem to drop content.

Usage:
    stack docs reformat <id> [--raw]     --raw prints markdown only (pipe-friendly).
"""

HELP = "Run the reformatter on a filed document (dry, no writes)"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import dispatch  # noqa: E402


def run(args, stacklet, config):
    if not config["is_healthy"]():
        return {"error": "Docs is not running — start it with 'stack up docs'"}
    argv = sys.argv[3:]
    return dispatch("reformat", *argv)
