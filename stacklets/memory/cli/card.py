"""stack memory card <id> [<id> ...]: cards of the family memory, with their links and history.

    stack memory card topic/camping
    stack memory card diary/b8211de8cb capture/B5o6SGEFyc person/lisa

Several ids open several cards in one call; together they get the text of
one card, shared. A card cut short that way shows more when opened alone.

Shows the card's line, where it lives, its last changes (who, when, what),
the cards it links to and the cards that link to it, the views the wiki
compiled about it (a person's page), then its text. Ids
come from `stack memory find`, `stack memory links` or another card. A
version (`@a1b2c3d`) that moved since you last read a card means it changed.

Exit codes: 0 shown, 1 no such card.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _graph import graph_for, take_audience  # noqa: E402

HELP = "Show cards of the family memory by id, with their links and history"

# A result is read again by every later call of a turn: several cards together
# get what one gets, and one alone gets the most the agent's tool passes on.
_TEXT_CHARS_TOGETHER = 3000
_TEXT_CHARS_ALONE = 6000
_LINKS_SHOWN = 10
_FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


def run(args, stacklet, config):
    args, audience = take_audience(args)
    refs = [a for a in args if not a.startswith("-")]
    if not refs:
        print("usage: stack memory card <id> [<id> ...], as `stack memory find` shows them",
              file=sys.stderr)
        sys.exit(2)
    graph = graph_for(config, audience)
    if graph is None:
        return {"error": "no family memory found; is the memory stacklet installed?"}
    found = [(ref, graph.card(ref)) for ref in refs]
    missing = [ref for ref, card in found if card is None]
    cards = [card for _, card in found if card is not None]
    if not cards:
        print(f"No card {', '.join(map(repr, missing))}. Find one with `stack memory find <words>`.")
        sys.exit(1)
    alone = len(cards) == 1
    budget = _TEXT_CHARS_ALONE if alone else _TEXT_CHARS_TOGETHER // len(cards)
    print("\n\n".join(_shown(card, graph, budget, alone=alone) for card in cards))
    if missing:
        print(f"\nNo card {', '.join(map(repr, missing))}.")
    return {"cards": [c.id for c in cards], "versions": [c.version for c in cards]}


def _shown(card, graph, budget: int, *, alone: bool) -> str:
    lines = [card.line(), f"file: {card.path}"]
    for change in card.changes[:3]:
        lines.append(f"changed {change.day} by {change.who} @{change.sha}: {change.subject}")
    lines += _links("links to", card.links_out, graph)
    lines += _links("linked from", card.links_in, graph)
    lines += [f"view: {v.path} ({v.view}, compiled by the wiki)" for v in card.views]
    text = _FRONTMATTER.sub("", card.text).strip()
    if len(text) > budget:
        rest = (f"`stack memory card {card.id}` alone shows more" if not alone
                else "not shown")
        text = text[:budget] + f"\n… {len(text) - budget} more characters, {rest}"
    return "\n".join(lines) + "\n\n" + text


def _links(label: str, ids: set[str], graph) -> list[str]:
    if not ids:
        return []
    cards = sorted((graph.cards[i] for i in ids if i in graph.cards),
                   key=lambda c: c.day or "", reverse=True)
    shown = "; ".join(f"{c.id} ({c.title})" for c in cards[:_LINKS_SHOWN])
    more = f"; and {len(cards) - _LINKS_SHOWN} more" if len(cards) > _LINKS_SHOWN else ""
    return [f"{label}: {shown}{more}"]
