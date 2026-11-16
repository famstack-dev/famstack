"""Progress lines of the diary command, on stderr.

    err("  12 recording(s), 3 to decode")

Every step reports what it does on stderr, because stdout carries the
JSON report the archivist posts from (`--json`) and the pages a dry run
prints.
"""

from __future__ import annotations

import sys


def err(msg: str) -> None:
    print(msg, file=sys.stderr)
