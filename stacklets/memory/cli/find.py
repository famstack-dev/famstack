"""stack memory find <words>: the family memory's cards that match, newest weighing most.

    stack memory find zelt kaputt                 cards about the broken tent
    stack memory find schule --type diary         only diary entries
    stack memory find --since 2026-10-01          everything changed since then
    stack memory find --person lisa --since 2026-10-06 --until 2026-10-12 --sort newest
                                                  what was about Lisa that week, newest first

One line per card: its id, type, date, title, version and link count. A
card is opened with `stack memory card <id>`, its neighbours with
`stack memory links <id>`. Recent cards rank higher, each type ageing at
its own pace (`graph.HALF_LIFE_DAYS`).

Exit codes: 0 found, 1 nothing found, 2 bad arguments.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _graph import graph_for, take_audience  # noqa: E402

HELP = "Find the family memory's cards by words, newest weighing most"


def run(args, stacklet, config):
    parser = argparse.ArgumentParser(prog="stack memory find", add_help=False)
    parser.add_argument("words", nargs="*")
    parser.add_argument("--type", default="")
    parser.add_argument("--since", default="")
    parser.add_argument("--until", default="")
    parser.add_argument("--person", default="")
    parser.add_argument("--sort", choices=("relevant", "newest"), default="relevant")
    parser.add_argument("--limit", type=int, default=8)
    args, audience = take_audience(args)
    try:
        ns = parser.parse_args(args)
    except SystemExit:
        print(__doc__.split("\n\n")[1].strip(), file=sys.stderr)
        sys.exit(2)
    graph = graph_for(config, audience)
    if graph is None:
        return {"error": "no family memory found; is the memory stacklet installed?"}
    cards = graph.find(" ".join(ns.words), kind=ns.type, since=ns.since, until=ns.until,
                       person=ns.person, newest=ns.sort == "newest", limit=ns.limit)
    if not cards:
        print("No card matches. Try other or fewer words.")
        sys.exit(1)
    print("\n".join(c.line() for c in cards))
    return {"cards": [c.id for c in cards]}
