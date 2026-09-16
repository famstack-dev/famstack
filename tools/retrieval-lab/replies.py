#!/usr/bin/env python3
"""Print what the agent actually answered, per question, per backend.

    uv run --extra test python tools/retrieval-lab/replies.py [id ...]

Call counts and wall time are the easy half. Whether the answer was
right is the half that decides anything, and no script can score it:
an answer can carry the correct total and still be wrong about which
bills it added. So this strips the runtime chatter and prints the
replies side by side for a human to read against `expect`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def answer(reply: str) -> str:
    """Drop nanobot's banner and thinking lines, keep the reply."""
    kept = [
        line for line in reply.splitlines()
        if line.strip()
        and not line.startswith(("✻", "🐈", "[llm-state]"))
    ]
    return "\n".join(kept).strip()


def main() -> None:
    rows = json.loads(
        (HERE / "out" / "agent-ab.json").read_text(encoding="utf-8"))
    wanted = sys.argv[1:]
    for scenario in dict.fromkeys(r["id"] for r in rows):
        if wanted and scenario not in wanted:
            continue
        group = [r for r in rows if r["id"] == scenario]
        print("\n" + "=" * 72)
        print(f"{scenario}  ({group[0]['kind']})")
        print(f"Q: {group[0]['q']}")
        print(f"EXPECT: {group[0]['expect']}")
        for row in group:
            print(f"\n--- {row['backend']}  "
                  f"({row['llm_calls']} calls, {row['wall_s']}s) ---")
            print(answer(row["reply"]) or "(no reply)")


if __name__ == "__main__":
    main()
