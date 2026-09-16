#!/usr/bin/env python3
"""Print one question kind's results side by side, for reading a regression.

    uv run --extra test python tools/retrieval-lab/inspect.py compound_tail

The summary tables say a kind got worse. This says which question, what
each engine put first, and where the right page ended up -- which is the
difference between a finding and a number that moved.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    kinds = sys.argv[1:] or ["compound_tail"]
    data = json.loads(
        (HERE / "out" / "results.json").read_text(encoding="utf-8"))
    for kind in kinds:
        print(f"\n=== {kind} ===")
        regex = {r["q"]: r for r in data["queries"]["regex"]
                 if r["class"] == kind}
        fts5 = {r["q"]: r for r in data["queries"]["fts5"]
                if r["class"] == kind}
        for question, rx in regex.items():
            ft = fts5[question]
            print(f"\n{question}")
            print(f"  gold  {', '.join(rx['gold']) or '(none)'}")
            print(f"  regex rank={rx['rank']} hits={rx['hits']:>2} "
                  f"top={rx['top']}")
            print(f"  fts5  rank={ft['rank']} hits={ft['hits']:>2} "
                  f"top={ft['top']}")


if __name__ == "__main__":
    main()
