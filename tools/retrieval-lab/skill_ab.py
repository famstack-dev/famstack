#!/usr/bin/env python3
"""Compare two versions of the family-memory skill, on one search engine.

    uv run --extra test python tools/retrieval-lab/skill_ab.py --repeat 3

Everything measured so far changed what answers a query. This changes
the query. The search log from the earlier runs says the agent asks in
the wrong language: it converses in German, searches in English, and
those searches come back empty about twice as often. That is a prompt
problem, and prompts are cheaper to change than engines.

**The metric, fixed before running.** Dead-end rate: the share of
searches that returned nothing, read straight out of the lab-api search
log rather than out of a reply somebody had to interpret. The skill
change targets exactly that, so it is the number that can falsify it.
Iterations and answers are reported alongside, because a skill that
cuts dead ends by sending the agent round more loops has not helped.

The baseline skill is materialised from git rather than kept as a
second copy in the tree, so it cannot drift out of step with what
shipped.

Prerequisites: one lab-api on 42011 serving the engine under test, and
proxy.py. Both arms reset the rig, so the vault is reseeded per arm and
nothing carries over.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RIG = REPO / "tools" / "agent-lab" / "rig"
SKILLS = "stacklets/agent/workspace/skills"

sys.path.insert(0, str(HERE))
from agent_ab import SCENARIOS, run_turn  # noqa: E402


def materialise_baseline(ref: str, out: Path) -> Path:
    """Check the pre-change skills tree out of git, into a scratch dir."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    listing = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", f"{ref}:{SKILLS}"],
        cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    for rel in listing:
        blob = subprocess.run(
            ["git", "show", f"{ref}:{SKILLS}/{rel}"],
            cwd=REPO, capture_output=True, text=True, check=True).stdout
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(blob, encoding="utf-8")
    return out


def reset(corpus: Path, skills: Path) -> None:
    subprocess.run(
        [sys.executable, str(RIG / "rig.py"), "reset",
         "--corpus", str(corpus), "--skills", str(skills)],
        check=True, capture_output=True)


def dead_ends(log: Path) -> dict:
    """What the agent asked for, and how often it got nothing back."""
    if not log.exists():
        return {"searches": 0, "empty": 0, "rate": 0.0}
    records = [json.loads(line) for line in
               log.read_text(encoding="utf-8").splitlines() if line.strip()]
    empty = sum(1 for r in records if r["exit"] != 0)
    return {
        "searches": len(records),
        "empty": empty,
        "rate": empty / len(records) if records else 0.0,
        "empty_queries": [r["query"] for r in records if r["exit"] != 0],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--baseline-ref", default="HEAD",
                        help="git ref holding the pre-change skill")
    parser.add_argument("--corpus",
                        default=str(HERE / "out" / "rig-vault"))
    # One port per arm, because the miss explanation is a server flag
    # and the skill line that reacts to it is meaningless without it.
    # The two ship together, so they are measured together.
    parser.add_argument("--port-before", type=int, default=42013)
    parser.add_argument("--port-after", type=int, default=42011)
    parser.add_argument("--log",
                        default=str(RIG / "state" / "search-regex.jsonl"))
    ns = parser.parse_args()

    arms = {
        "before": (materialise_baseline(ns.baseline_ref,
                                        HERE / "out" / "skills-baseline"),
                   ns.port_before),
        "after": (REPO / SKILLS, ns.port_after),
    }

    out: list[dict] = []
    summary: dict[str, dict] = {}
    log = Path(ns.log)
    for arm, (skills, port) in arms.items():
        reset(Path(ns.corpus), skills)
        if log.exists():
            log.unlink()
        for run in range(ns.repeat):
            for scenario in SCENARIOS:
                session = f"skill{run}:{scenario['id']}:{arm}"
                print(f"[{arm:>6} run {run}] {scenario['id']}", flush=True)
                turn = run_turn(scenario["q"], arm, port, session)
                out.append({**scenario, **turn, "run": run, "arm": arm})
                print(f"          {turn['llm_calls']} calls, "
                      f"{turn['wall_s']}s", flush=True)
        summary[arm] = dead_ends(log)
        shutil.copy(log, HERE / "out" / f"search-{arm}.jsonl")

    (HERE / "out" / "skill-ab.json").write_text(
        json.dumps({"turns": out, "searches": summary}, indent=2,
                   ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 64)
    print(f"{'arm':>7}  {'searches':>9}  {'dead ends':>10}  {'rate':>6}  "
          f"{'calls':>7}")
    for arm in arms:
        s = summary[arm]
        calls = sum(r["llm_calls"] or 0 for r in out if r["arm"] == arm)
        print(f"{arm:>7}  {s['searches']:>9}  {s['empty']:>10}  "
              f"{s['rate']:>5.0%}  {calls:>7}")
    print(f"\ndetail: {HERE / 'out' / 'skill-ab.json'}")


if __name__ == "__main__":
    main()
