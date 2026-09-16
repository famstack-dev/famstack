#!/usr/bin/env python3
"""Replay the agent's own queries against each version of the engine.

    uv run --extra test python tools/retrieval-lab/replay.py

Every other measurement here uses a gold set somebody wrote. This one
uses the queries the agent actually sent, recovered from the lab-api
search logs, and asks the one question a gold set cannot answer: of the
searches that really happened, how many came back empty before the
change and how many after.

Zero results is the right metric for these two fixes. Ranking decides
which page is first; these decide whether there is a page at all, and
an agent can iterate its way past bad ordering but not past nothing.

Each fix is scored on its own, because attributing a gain to the wrong
change has already happened twice in this work.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(REPO / "stacklets" / "memory"))

from lib import search_memory  # noqa: E402

# (fold diacritics, search frontmatter values)
ARMS: dict[str, tuple[bool, bool]] = {
    "old (body only, byte literal)": (False, False),
    "folding only": (True, False),
    "title/tags only": (False, True),
    "both (shipped)": (True, True),
}


def collect(paths: list[Path]) -> list[str]:
    """Distinct queries, in the order the agent first sent them."""
    queries: list[str] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                queries.append(json.loads(line)["query"])
    return list(dict.fromkeys(queries))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", default=str(
        REPO / "tools" / "agent-lab" / "rig" / "state" / "vault"))
    parser.add_argument("--logs", nargs="*", default=None)
    ns = parser.parse_args()

    if ns.logs:
        paths = [Path(p) for p in ns.logs]
    else:
        state = REPO / "tools" / "agent-lab" / "rig" / "state"
        paths = sorted(state.glob("search-*.jsonl")) + \
            sorted((HERE / "out").glob("search-*.jsonl"))
    paths = [p for p in paths if p.exists()]
    if not paths:
        sys.exit("no search logs found -- run the agent rig first")

    vault = Path(ns.vault)
    queries = collect(paths)
    print(f"{len(queries)} distinct real agent queries, "
          f"{len(list(vault.rglob('*.md')))} pages\n")

    for name, (fold, frontmatter) in ARMS.items():
        dead = sum(1 for q in queries
                   if not search_memory(q, vault, limit=5,
                                        fold_diacritics=fold,
                                        search_frontmatter=frontmatter))
        print(f"{name:<30} zero results: {dead:>3}/{len(queries)} "
              f"({dead / len(queries):.0%})")


if __name__ == "__main__":
    main()
