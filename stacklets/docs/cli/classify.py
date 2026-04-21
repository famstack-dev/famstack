"""stack docs classify <id> — classify a filed document and apply to Paperless.

Runs the archivist's classify prompt against the doc's OCR text and
applies the result (title, topic/person tags, type, correspondent, date)
to Paperless. No reformat, no mirror — scoped to classification only.

Use `--dry-run` (or `--dry`) to preview without writes. Use `--json` for
pipe-friendly raw LLM output (implies dry).

Usage:
    stack docs classify <id> [--dry | --dry-run] [--json]

Examples:
    stack docs classify 42                  # apply classification
    stack docs classify 42 --dry-run        # preview, no writes
    stack docs classify 42 --json | jq      # raw JSON, pipeable
"""

HELP = "Classify a filed document and apply to Paperless"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import dispatch  # noqa: E402


def run(args, stacklet, config):
    if not config["is_healthy"]():
        return {"error": "Docs is not running — start it with 'stack up docs'"}
    argv = sys.argv[3:]
    return dispatch("classify", *argv)
