"""Where the family memory does not meet its format yet (ADR-016, ADR-017).

Each finding is something a step of ADR-016's order removes: cards without
a `card_id`, ids two files claim, links to a file the memory repo does not
have, files that are no card type. The report empties as the steps land, so
a memory that meets the format gives an empty one.

    report = format_report(load_graph(vault))
    report["findings"]            # 0: nothing to fix
    report["links_to_nothing"]    # [{"card", "title", "path", "link", "target"}, ...]

One report, two surfaces: the cards API (`/report`, which the card browser
shows next to the cards) and `stack memory check-format`. It reads the
admin's graph, every card: a report filtered by who is looking would hide
what needs fixing. It writes nothing.
"""

from __future__ import annotations

from graph import Card, Graph, links_of
from stack.cards import CARD_TYPES, card_id_from
from stack.frontmatter import FrontmatterError, parse


def format_report(graph: Graph) -> dict:
    """What in `graph`'s memory does not meet the format, by kind of finding."""
    cards = sorted(graph.cards.values(), key=lambda c: c.path)
    report = {
        "cards": len(cards),
        "without_card_id": _without_card_id(cards),
        "shared_ids": _shared_card_ids(cards),
        "links_to_nothing": _links_to_nothing(graph, cards),
        "no_card_type": [f for f in map(_type_problem, cards) if f],
    }
    report["findings"] = (sum(report["without_card_id"].values()) + len(report["shared_ids"])
                          + len(report["links_to_nothing"]) + len(report["no_card_type"]))
    return report


def _without_card_id(cards: list[Card]) -> dict[str, int]:
    """How many cards of each type carry no `card_id` (ADR-016 §2)."""
    counts: dict[str, int] = {}
    for card in cards:
        if not card_id_from(card.meta):
            counts[card.type] = counts.get(card.type, 0) + 1
    return dict(sorted(counts.items()))


def _shared_card_ids(cards: list[Card]) -> list[dict]:
    """Each `card_id` two or more files carry, with those files (ADR-016 §2).

    Read from the stored ids: two cards without one are not one card for
    sharing a name, they are two cards still waiting for their ids.
    """
    holders: dict[str, list[str]] = {}
    for card in cards:
        if card_id := card_id_from(card.meta):
            holders.setdefault(card_id, []).append(card.path)
    return [{"id": cid, "paths": paths} for cid, paths in sorted(holders.items()) if len(paths) > 1]


def _links_to_nothing(graph: Graph, cards: list[Card]) -> list[dict]:
    """Every link from a card to a file the memory repo does not have (ADR-017 §3)."""
    return [{"card": card.id, "title": card.title, "path": card.path, "link": link, "target": target}
            for card in cards for link, target in links_of(card) if graph.at(target) is None]


def _type_problem(card: Card) -> dict | None:
    """A file with no frontmatter, frontmatter that does not parse, or a `type`
    outside ADR-017 §6; None for a card whose type is one."""
    if not card.text.startswith("---"):
        return {"card": card.id, "path": card.path, "problem": "no frontmatter"}
    try:
        kind = str(parse(card.text).get("type") or "").strip()
    except FrontmatterError as e:
        return {"card": card.id, "path": card.path, "problem": f"frontmatter does not parse: {e}"}
    if not kind:
        return {"card": card.id, "path": card.path, "problem": "no type"}
    if kind not in CARD_TYPES:
        return {"card": card.id, "path": card.path, "problem": f"type {kind!r} is not a card type"}
    return None
