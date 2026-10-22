"""Where the family memory does not meet its format yet (ADR-016, ADR-017).

Each finding is something a step of ADR-016's order removes: cards without
a `card_id`, ids two files claim, links to a file the memory repo does not
have, files that are no card type, tags that are no topic id and no
keyword, a `kind` outside its type's set, a note whose sources and sidecar
do not pair up, a sidecar whose card is gone (ADR-018 §3), a sidecar
that does not meet `schemas/sidecar.json` (`sidecar_schema.py`), and
fields the card type's schema does not name (`card_fields.py`). The report empties as
the steps land, so a memory that meets the format gives an empty one.

    report = format_report(load_graph(vault))
    report["findings"]            # 0: nothing to fix
    report["links_to_nothing"]    # [{"card", "title", "path", "link", "target"}, ...]

One report, two surfaces: the cards API (`/report`, which the card browser
shows next to the cards) and `stack memory check-format`. It reads the
admin's graph, every card: a report filtered by who is looking would hide
what needs fixing. It writes nothing.
"""

from __future__ import annotations

from card_fields import unknown_fields
from graph import Card, Graph, links_of, tags_of
from note_card import is_legacy_note
from sidecar_schema import sidecar_problems
from stack.cards import CARD_TYPES, card_id_from, kind_problem
from stack.cards.sources import MACHINE_FIELDS, has_sources, quotes_of, sidecar_path
from stack.frontmatter import FrontmatterError, parse
from stack.tags import is_tag


def format_report(graph: Graph) -> dict:
    """What in `graph`'s memory does not meet the format, by kind of finding."""
    cards = sorted(graph.cards.values(), key=lambda c: c.path)
    report = {
        "cards": len(cards),
        "without_card_id": _without_card_id(cards),
        "shared_ids": _shared_card_ids(cards),
        "links_to_nothing": _links_to_nothing(graph, cards),
        "no_card_type": [f for f in map(_type_problem, cards) if f],
        "invalid_tags": _invalid_tags(graph, cards),
        "wrong_kind": [f for f in map(_kind_problem, cards) if f],
        "note_sources": [f for f in map(_sources_problem, cards) if f],
        "lone_sidecars": list(graph.lone_sidecars),
        "invalid_sidecars": [f for f in map(_sidecar_problem, cards) if f],
        "unknown_fields": _unknown_fields(cards),
    }
    report["findings"] = (sum(report["without_card_id"].values()) + len(report["shared_ids"])
                          + len(report["links_to_nothing"]) + len(report["no_card_type"])
                          + sum(len(f["tags"]) for f in report["invalid_tags"])
                          + len(report["wrong_kind"]) + len(report["note_sources"])
                          + len(report["lone_sidecars"])
                          + sum(len(f["problems"]) for f in report["invalid_sidecars"])
                          + sum(len(f["fields"]) for f in report["unknown_fields"]))
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


def _invalid_tags(graph: Graph, cards: list[Card]) -> list[dict]:
    """Each card's tags that are neither a topic's id nor a keyword (ADR-017 §4):
    a topic's label (`Gesundheit`), a tag with capitals or spaces, `Person: Homer`."""
    found = []
    for card in cards:
        if invalid := [t for t in tags_of(card) if not is_tag(t, graph.ontology)]:
            found.append({"card": card.id, "path": card.path, "tags": invalid})
    return found


# Fields `wrong_kind` already reports, whose finding says more than "unknown".
_KIND_FIELDS = frozenset({"kind", "medium"})


def _unknown_fields(cards: list[Card]) -> list[dict]:
    """Each card's fields its type's schema does not name: a write refuses
    them until they are gone (`stack memory card edit --remove <field>`).

    A field another finding already names is left to it: `kind` and
    `medium` to `wrong_kind`, and the machine fields of a note in the
    format before ADR-018 to `note_sources`, since migration `memory/0008`
    moves them into its sidecar.
    """
    found = []
    for card in cards:
        reported = _KIND_FIELDS | (MACHINE_FIELDS if _old_note(card) else frozenset())
        if unknown := [f for f in unknown_fields(card.meta) if f not in reported]:
            found.append({"card": card.id, "path": card.path, "fields": unknown})
    return found


def _old_note(card: Card) -> bool:
    return card.type == "note" and card.sidecar is None and is_legacy_note(card.text)


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


def _kind_problem(card: Card) -> dict | None:
    """A card whose `kind` is outside its type's set, or that needs one and
    lacks it (ADR-017 §6, `stack.cards.kind_problem`); None for a card that
    is fine. A file that is no card type is `_type_problem`'s."""
    if str(card.meta.get("type") or "").strip() not in CARD_TYPES:
        return None
    if problem := kind_problem(card.path, card.meta):
        return {"card": card.id, "path": card.path, "problem": problem}
    return None


def _sidecar_problem(card: Card) -> dict | None:
    """A card's sidecar with what in it `schemas/sidecar.json` does not
    allow (ADR-018 §3), each value named with the values or the form it
    may take; None for a card with no sidecar or a sidecar that meets it."""
    if card.sidecar is None:
        return None
    if problems := sidecar_problems(card.sidecar):
        return {"card": card.id, "path": sidecar_path(card.path), "problems": problems}
    return None


def _sources_problem(card: Card) -> dict | None:
    """A note whose sources do not meet ADR-018 §3, None for one that does.

    A note filed in the format before (its words in the old sections),
    one with `## Sources` and no sidecar, and one whose quotes and sidecar
    sources differ in number, so that which quote is whose is a guess. A
    note written in the memory has neither and is fine.
    """
    if card.type != "note":
        return None
    def found(problem: str) -> dict:
        return {"card": card.id, "title": card.title, "path": card.path, "problem": problem}
    if card.sidecar is None:
        if is_legacy_note(card.text):
            return found("in the format before ADR-018: its sources are not in a sidecar yet")
        return found("no sidecar for its sources") if has_sources(card.text) else None
    records = [r for r in card.sidecar.get("sources") or [] if isinstance(r, dict)]
    quotes = quotes_of(card.text)
    if len(records) != len(quotes):
        return found(f"{len(quotes)} quote(s) under ## Sources and {len(records)} "
                     "source(s) in its sidecar")
    return None
