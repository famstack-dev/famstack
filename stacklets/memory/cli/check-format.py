"""stack memory check-format: where the family memory does not meet its format yet.

    stack memory check-format
    stack memory check-format --json

Reads every card of the memory repo, as the admin, and lists what ADR-016
and ADR-017 ask for that is not there yet: cards without a `card_id`
(counted per type), ids two files claim, links to a file the memory repo
does not have, and files with no frontmatter or a `type` that is no card
type. The card browser shows the same report. Nothing is changed.

Exit codes: 0 nothing to fix, 1 something to fix or no memory found.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _graph import graph_for  # noqa: E402

from format_check import format_report  # noqa: E402

HELP = "Report where the family memory does not meet its format yet"


def run(args, stacklet, config):
    graph = graph_for(config)
    if graph is None:
        return {"error": "no family memory found; is the memory stacklet installed?"}
    report = format_report(graph)
    print("\n".join(_shown(report)))
    if report["findings"]:
        return {"error": f"{report['findings']} to fix", **report}
    return report


def _shown(report: dict) -> list[str]:
    """The report as people read it, one section per kind of finding."""
    if not report["findings"]:
        return [f"{report['cards']} cards. Nothing to fix: the memory meets its format."]
    lines = [f"{report['cards']} cards, {report['findings']} to fix."]
    if missing := report["without_card_id"]:
        lines += ["", f"Without a card_id: {sum(missing.values())}",
                  "  " + ", ".join(f"{kind} {n}" for kind, n in missing.items())]
    if shared := report["shared_ids"]:
        lines += ["", "One id, several files:"]
        lines += [f"  {s['id']}  {', '.join(s['paths'])}" for s in shared]
    if dead := report["links_to_nothing"]:
        lines += ["", "Links to nothing in the memory repo:"]
        lines += [f"  {d['card']}  {d['link']}  ({d['target']} is not there)" for d in dead]
    if untyped := report["no_card_type"]:
        lines += ["", "Not a card type:"]
        lines += [f"  {u['path']}  {u['problem']}" for u in untyped]
    return lines
