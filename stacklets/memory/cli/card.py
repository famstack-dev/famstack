"""stack memory card <id> [<id> ...]: cards of the family memory, with their links and history.

    stack memory card topic/camping
    stack memory card diary/b8211de8cb capture/B5o6SGEFyc person/lisa
    stack memory card note-7f3a9c21e4

Several ids open several cards in one call; together they get the text of
one card, shared. A card cut short that way shows more when opened alone.

Shows the card's line, where it lives, its last changes (who, when, what),
the cards it links to and the cards that link to it, the views the wiki
compiled about it (a person's page), then its text. Ids come from `stack
memory find`, `stack memory links` or another card, or are a card's own
`card_id` (ADR-016). An id a card had before (`former_card_ids`) opens the
card that has it now and says so; an id of a card that was removed says
when, and by whom. A version (`@a1b2c3d`) that moved since you last read a
card means it changed.

Exit codes: 0 shown, 1 no such card.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _graph import graph_for, take_audience  # noqa: E402
from graph import readable  # noqa: E402

from lib import vault_path_for  # noqa: E402
from stack.cards import find_by_card_id, is_card_id, removal_of  # noqa: E402

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
    vault = vault_path_for(Path(config["data_dir"]))
    shared = ((config or {}).get("stack", {}).get("memory", {}) or {}).get("shared_bucket", "family")
    asked: dict[str, str] = {}
    gone: dict[str, str] = {}
    found = []
    for ref in refs:
        card, said = _card(ref, graph, vault, audience, shared)
        found.append((ref, card))
        if card is not None and said:
            asked[ref] = said
        elif said:
            gone[ref] = said
    missing = [ref for ref, card in found if card is None and ref not in gone]
    cards = [card for _, card in found if card is not None]
    for said in gone.values():
        print(said)
    if not cards:
        if missing:
            print(f"No card {', '.join(map(repr, missing))}. Find one with `stack memory find <words>`.")
        sys.exit(1)
    alone = len(cards) == 1
    budget = _TEXT_CHARS_ALONE if alone else _TEXT_CHARS_TOGETHER // len(cards)
    print("\n\n".join(_shown(card, graph, budget, alone=alone) for card in cards))
    for said in asked.values():
        print(f"\n{said}")
    if missing:
        print(f"\nNo card {', '.join(map(repr, missing))}.")
    result: dict[str, object] = {"cards": [c.id for c in cards],
                                 "versions": [c.version for c in cards]}
    if asked:
        result["asked"] = asked
    if gone:
        result["gone"] = gone
    return result


def _card(ref: str, graph, vault: Path, audience, shared: str):
    """The card `ref` names, and a line to say about how it was found.

    A ref that is a card id is looked up in the memory repo, by its id or
    a former one, and shown from the graph by its path, so an audience
    that may not read it sees no card. One no card carries any more is
    answered from the history, for an audience that could have read it.
    """
    if not is_card_id(ref):
        return graph.card(ref), ""
    hit = find_by_card_id(ref, root=vault)
    if hit is not None:
        card = graph.card(hit.path)
        said = f"Asked for {ref}: an id this card had before; it is {hit.card_id} now." \
            if card is not None and hit.former else ""
        return card, said
    removal = removal_of(ref, repo=vault)
    if removal is None or (audience is not None
                           and not readable(removal.path, audience, shared_bucket=shared)):
        return None, ""
    return None, (f"Card {ref} is gone: {removal.who} removed it on {removal.day} "
                  f"(@{removal.sha}: {removal.subject}).")


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
