"""Shared helpers for docs CLI commands.

Stderr logger, doc-id parsing, and the dry-run flag set every write-capable
command honours. Kept small on purpose — command modules only reach in here
for utilities that would otherwise be copy-pasted across files.
"""

from __future__ import annotations

import sys


_DRY_FLAGS = ("--dry-run", "--dry")


def err(msg: str) -> None:
    print(msg, file=sys.stderr)


def is_dry(argv: list[str]) -> bool:
    return any(f in argv for f in _DRY_FLAGS)


def parse_doc_id(raw: str) -> int | None:
    try:
        return int(raw)
    except ValueError:
        err(f"Invalid document id: {raw!r} (must be an integer)")
        return None
