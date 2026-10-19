"""Shared by `find`, `card`, `links` and `check-format`: the graph of the family memory.

Its cards are the vault's files; the brain only adds the views compiled about them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph import Graph, load_graph  # noqa: E402

from lib import brain_path_for, vault_path_for  # noqa: E402


# Who will see the answer: `--audience marge` or `--audience bart,homer`. Only
# what they may all read is in the graph (`graph.readable`).
def take_audience(args: list[str]) -> tuple[list[str], frozenset[str] | None]:
    """`args` without `--audience <people>`, and the audience it named (None: not given)."""
    args = list(args or [])
    if "--audience" not in args:
        return args, None
    i = args.index("--audience")
    value = args[i + 1] if i + 1 < len(args) else ""
    del args[i:i + 2]
    return args, frozenset(p.strip().lower() for p in value.split(",") if p.strip())


def graph_for(config, audience: frozenset[str] | None = None) -> Graph | None:
    data_dir = (config or {}).get("data_dir")
    if not data_dir:
        return None
    brain = brain_path_for(Path(data_dir))
    vault = vault_path_for(Path(data_dir))
    if not vault.exists():
        return None
    shared = ((config or {}).get("stack", {}).get("memory", {}) or {}).get("shared_bucket", "family")
    return load_graph(vault, views=brain if brain.exists() else None, audience=audience,
                      shared_bucket=shared)
