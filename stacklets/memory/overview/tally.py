"""What a page's cards link to, tallied, and how many cards of each type it has.

    links(graph, cards, leave_out="marge")
    # {"people": [{"id": "lisa", "title": "Lisa Simpson", "count": 1}],
    #  "topics": [{"id": "topic-camping", "title": "Camping", "count": 2}],
    #  "senders": [{"id": "correspondent-kwik-e-mart", "title": "Kwik-E-Mart", "count": 1}]}
    counts(cards)   # {"note": 3, "document": 2}

The memory is a graph: every page ends with the people, topics and senders
its cards name, most first, each a link to its own page. Only what has a
page is tallied, as the graph resolved each card's names
(`Card.person_pages`, `Card.sender_card`): a name in `persons` with no
person card the audience may read, or a sender with no sender card, has
nowhere to link to.
"""

from __future__ import annotations

from collections import Counter

from ..graph import Card, Graph
from .scope import person_of

__all__ = ["counts", "links"]


def links(graph: Graph, cards: list[Card], *, leave_out: str = "") -> dict:
    """The people, topics and senders `cards` name, each with how many of
    them name it, most first, then by title.

    `leave_out` is the id of the person the page is about (`marge`): her own
    page does not list her among the people she is often with.
    """
    people: Counter = Counter()
    titles: dict[str, str] = {}
    for card in cards:
        # Two names of one person on a card ("Mom", "marge") count once.
        for page in set(card.person_pages.values()) - {"", leave_out}:
            person = person_of(graph, page)
            if person is not None:
                people[person.id] += 1
                titles[person.id] = person.title
    topics = Counter(t for c in cards for t in c.links_out
                     if t in graph.cards and graph.cards[t].type == "topic")
    titles |= {t: graph.cards[t].title for t in topics}
    senders = Counter(c.sender_card for c in cards if c.sender_card)
    titles |= {s: graph.cards[s].title for s in senders}
    return {"people": _ranked(people, titles), "topics": _ranked(topics, titles),
            "senders": _ranked(senders, titles)}


def counts(cards: list[Card]) -> dict[str, int]:
    """How many of `cards` there are of each type, most first."""
    return dict(Counter(c.type for c in cards).most_common())


def _ranked(tally: Counter, titles: dict[str, str]) -> list[dict]:
    ranked = sorted(tally.items(), key=lambda kv: (-kv[1], titles.get(kv[0], "")))
    return [{"id": key, "title": titles.get(key, ""), "count": n} for key, n in ranked]
