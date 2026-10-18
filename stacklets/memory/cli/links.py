"""stack memory links <id> [--depth 2]: the cards around a card.

    stack memory links person/lisa
    stack memory links topic/camping --depth 2

One line per card: how far it is, how it is linked, and its card line.
Nearest first, newest first within the same distance.

Exit codes: 0 shown, 1 no such card or no links.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _graph import graph_for, take_audience  # noqa: E402

HELP = "Show the cards linked to a card of the family memory"

_SHOWN = 20


def run(args, stacklet, config):
    args, audience = take_audience(args)
    depth = 1
    if "--depth" in args:
        i = args.index("--depth")
        try:
            depth = max(1, min(int(args[i + 1]), 3))
        except (IndexError, ValueError):
            print("usage: stack memory links <id> [--depth 1-3]", file=sys.stderr)
            sys.exit(2)
        del args[i:i + 2]
    ref = " ".join(a for a in args if not a.startswith("-")).strip()
    graph = graph_for(config, audience)
    if graph is None:
        return {"error": "no family memory found; is the memory stacklet installed?"}
    found = graph.neighbours(ref, depth=depth)
    if not found:
        print(f"No links for {ref!r}. Find a card with `stack memory find <words>`.")
        sys.exit(1)
    found.sort(key=lambda f: (f[0], -(int((f[2].day or "0").replace("-", "") or 0))))
    print("\n".join(f"{d}  {how}  {c.line()}" for d, how, c in found[:_SHOWN]))
    if len(found) > _SHOWN:
        print(f"… and {len(found) - _SHOWN} more")
    return {"cards": [c.id for _, _, c in found[:_SHOWN]]}
