#!/usr/bin/env python3
"""Aggregate a repeated agent A/B, and check it was a fair fight.

    uv run --extra test python tools/retrieval-lab/compare.py \
        --results out/agent-ab-repeat.json \
        --logs ../agent-lab/rig/state/search-regex.jsonl \
               ../agent-lab/rig/state/search-fts5.jsonl

Two halves.

**The numbers, across repeats.** One run per cell cannot tell a real
difference from a dice roll, so this reports the spread rather than a
single value. A median that moves less than the run-to-run range has
not moved.

**Whether the comparison was fair.** The agent writes its own query for
each search, so the two backends never receive quite the same input.
If one arm happened to be asked better questions, a difference between
them says nothing about the engines. The search logs make that
visible: how many searches each arm was given, and how much the
queries overlap.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Sequence

HERE = Path(__file__).resolve().parent


def spread(values: Sequence[float]) -> str:
    if not values:
        return "-"
    if len(values) == 1:
        return f"{values[0]:g}"
    return f"{statistics.median(values):g} [{min(values):g}-{max(values):g}]"


def numbers(rows: list[dict]) -> None:
    backends = list(dict.fromkeys(r["backend"] for r in rows))
    ids = list(dict.fromkeys(r["id"] for r in rows))

    print(f"\n{'question':<18} {'backend':>8}  {'llm calls':>16}  "
          f"{'wall seconds':>20}")
    print("-" * 70)
    for qid in ids:
        for backend in backends:
            cell = [r for r in rows
                    if r["id"] == qid and r["backend"] == backend]
            calls = [r["llm_calls"] for r in cell if r["llm_calls"]]
            walls = [r["wall_s"] for r in cell if r["wall_s"]]
            print(f"{qid:<18} {backend:>8}  {spread(calls):>16}  "
                  f"{spread(walls):>20}")

    print(f"\n{'backend':>8}  {'total calls per run':>22}  "
          f"{'total wall per run':>22}  {'errors':>7}")
    for backend in backends:
        per_run = defaultdict(lambda: [0, 0.0])
        errors = 0
        for r in rows:
            if r["backend"] != backend:
                continue
            if r.get("exit"):
                errors += 1
            per_run[r.get("run", 0)][0] += r["llm_calls"] or 0
            per_run[r.get("run", 0)][1] += r["wall_s"] or 0
        calls = [v[0] for v in per_run.values()]
        walls = [round(v[1]) for v in per_run.values()]
        print(f"{backend:>8}  {spread(calls):>22}  {spread(walls):>22}  "
              f"{errors:>7}")


def fairness(log_paths: list[Path]) -> None:
    """Did both arms get asked comparable questions?"""
    by_backend: dict[str, list[dict]] = defaultdict(list)
    for path in log_paths:
        if not path.exists():
            print(f"\n(no search log at {path})")
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                by_backend[record["backend"]].append(record)

    if not by_backend:
        return

    print(f"\n{'backend':>8}  {'searches':>9}  {'median terms':>13}  "
          f"{'empty results':>14}  {'distinct queries':>17}")
    for backend, records in by_backend.items():
        terms = [len(r.get("keywords") or []) for r in records]
        empty = sum(1 for r in records if r["exit"] != 0)
        distinct = len({r["query"].lower() for r in records})
        print(f"{backend:>8}  {len(records):>9}  "
              f"{statistics.median(terms) if terms else 0:>13g}  "
              f"{empty:>14}  {distinct:>17}")

    vocab = {b: {w.lower() for r in rs for w in (r.get("keywords") or [])}
             for b, rs in by_backend.items()}
    names = list(vocab)
    if len(names) == 2:
        a, b = vocab[names[0]], vocab[names[1]]
        shared = len(a & b) / max(len(a | b), 1)
        print(f"\nkeyword vocabulary overlap between arms: {shared:.0%} "
              f"({len(a & b)} shared of {len(a | b)} distinct)")
        print("Low overlap means the arms were asked different things and "
              "the comparison is weaker than it looks.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results",
                        default=str(HERE / "out" / "agent-ab-repeat.json"))
    parser.add_argument("--logs", nargs="*", default=[])
    ns = parser.parse_args()

    rows = json.loads(Path(ns.results).read_text(encoding="utf-8"))
    numbers(rows)
    fairness([Path(p) for p in ns.logs])


if __name__ == "__main__":
    main()
