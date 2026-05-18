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


def parse_id_specs(args: list[str]) -> list[int] | None:
    """Expand a mix of single ids and inclusive ranges into a flat list.

    Accepted tokens:
      - `42`        -> [42]
      - `1-13`      -> [1, 2, ..., 13]
      - mixed:      `1 3-5 9` -> [1, 3, 4, 5, 9]

    Ranges are ascending and inclusive on both ends. Duplicates across
    tokens collapse to a single entry, order-preserving (first
    appearance wins). Returns None on any invalid token — the CLI
    command treats None as a usage error.

    Pure-lexical: this layer doesn't know whether an id exists in
    Paperless. The caller's get_doc lookup decides that, and may
    silently skip missing ids when iterating a range.
    """
    if not args:
        return []

    out: list[int] = []
    seen: set[int] = set()
    for raw in args:
        if not raw.strip():
            err(f"Invalid id spec: {raw!r}")
            return None
        if "-" in raw:
            parts = raw.split("-")
            if len(parts) != 2 or not parts[0] or not parts[1]:
                err(f"Invalid range: {raw!r} (expected <int>-<int>)")
                return None
            try:
                lo, hi = int(parts[0]), int(parts[1])
            except ValueError:
                err(f"Invalid range: {raw!r} (expected <int>-<int>)")
                return None
            if lo > hi:
                err(f"Invalid range: {raw!r} (start must be <= end)")
                return None
            ids: list[int] = list(range(lo, hi + 1))
        else:
            try:
                ids = [int(raw)]
            except ValueError:
                err(f"Invalid document id: {raw!r}")
                return None
        for n in ids:
            if n in seen:
                continue
            seen.add(n)
            out.append(n)
    return out
