"""stack docs classify <id> — dry-run classifier against a filed document.

Runs the archivist's classify prompt against the doc's OCR text and prints
the LLM's JSON output. Paperless is not touched, mirror is not updated.

Useful for:
  - Tuning the prompt (compare outputs across tweaks)
  - Debugging a misclassification (what did the LLM actually see?)
  - Validating a new model before switching the default

Usage:
    stack docs classify <id> [--json]    --json prints raw JSON (pipe-friendly).
"""

HELP = "Run the classifier on a filed document (dry, no writes)"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import dispatch  # noqa: E402


def run(args, stacklet, config):
    if not config["is_healthy"]():
        return {"error": "Docs is not running — start it with 'stack up docs'"}
    argv = sys.argv[3:]
    return dispatch("classify", *argv)
