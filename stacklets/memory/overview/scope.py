"""Which cards a page of the memory is about: a person's, a topic's, a sender's.

    person = person_of(graph, "bart")          # Person("bart", "person-bart", "Bart Simpson")
    mine = [c for c in entries(graph) if is_about(c, person)]
    camping = topic_of(graph, "topic-camping") # the topic card, or None
    cards = in_topic(entries(graph), camping)

Every function here reads a graph that already holds only what the audience
may read (`graph.load_graph(audience=...)`). What they decide is relevance,
never privacy: a card is about a person when it names them or sits in their
own folder. Who filed a card does not count, because a parent files the
children's papers, and those are the children's.

A page's lines are its *entries*: the cards that are things the family filed
or keeps (notes, documents, bookmarks, diary entries, lists). Person, topic
and sender cards are what a page links to, and a list's archive months are
part of their list, so none of them is an entry.
"""

from __future__ import annotations

from dataclasses import dataclass

from stack.cards import ARCHIVE, INDEX

from ..graph import Card, Graph, person_slug

__all__ = ["KINDS", "Person", "bucket_of", "document_kind", "entries", "in_topic", "is_about", "own_date", "own_folder",
           "person_of", "sender_name", "sender_of", "sent_by", "topic_of"]

# Cards a page links to rather than lists as a line.
_NOT_ENTRIES = frozenset({"person", "topic", "correspondent", ARCHIVE, INDEX})

# The kinds of entry, by their card type, in the order the menu's
# "Everything" lists them: what was filed first, then what is kept.
KINDS = ("document", "note", "diary", "email", "bookmark", "list")


@dataclass(frozen=True)
class Person:
    """Someone a page is about: their id (`bart`, as `persons` and the
    audience name them), their person card's id and its title.

        Person("bart", "person-bart", "Bart Simpson").as_json()
        # {"id": "bart", "card": "person-bart", "title": "Bart Simpson"}

    `card` and `title` are "" for someone without a person card.
    """

    id: str
    card: str = ""
    title: str = ""

    def as_json(self) -> dict:
        return {"id": self.id, "card": self.card, "title": self.title}


def entries(graph: Graph) -> list[Card]:
    """The graph's entries, newest first (by day, then title).

    A card without a day comes last. Example: a note of 2026-09-03 comes
    before a document of 2026-09-02; a person card is not there.
    """
    found = [c for c in graph.cards.values() if c.type not in _NOT_ENTRIES]
    return sorted(found, key=lambda c: (c.day or "", c.title), reverse=True)


def person_of(graph: Graph, ref: str) -> Person | None:
    """The person `ref` names (`bart`, `person-bart`, `Bart`), from their
    person card; None when the graph has no such card."""
    card_id = graph.person_id(ref)
    if not card_id:
        return None
    card = graph.cards[card_id]
    return Person(person_slug(card), card.id, card.title)


def is_about(card: Card, person: Person) -> bool:
    """Whether `card` names `person` or sits in their own folder.

    It names them in `persons` (`[lisa, marge]`) or links their person card.
    `family/notes/school-play.md` with `persons: [lisa, marge]` is about
    Marge; `marge/garden/notes/roses.md` is hers though it names no one.
    """
    return ((bool(person.card) and person.card in card.links_out)
            or person.id in (p.lower() for p in card.persons)
            or bucket_of(card) == person.id)


def topic_of(graph: Graph, ref: str) -> Card | None:
    """The topic card `ref` names (`topic-camping`), or None, also for a card of another type."""
    card = graph.card(ref)
    return card if card is not None and card.type == "topic" else None


def in_topic(cards: list[Card], topic: Card) -> list[Card]:
    """The cards of `cards` that belong to `topic`: filed in its folder, or
    naming it by a tag or `topics` (the graph links both)."""
    return [c for c in cards if topic.id in c.links_out]


def sender_of(graph: Graph, ref: str) -> Card | None:
    """The sender card `ref` names (`correspondent-kwik-e-mart`), or None."""
    card = graph.card(ref)
    return card if card is not None and card.type == "correspondent" else None


def sent_by(cards: list[Card], sender: Card) -> list[Card]:
    """The documents of `cards` from `sender`: those whose `correspondent`
    the graph resolved to its card (`Card.sender_card`)."""
    return [c for c in cards if c.type == "document" and c.sender_card == sender.id]


def sender_name(card: Card) -> str:
    """The sender a document names (`Kwik-E-Mart`), "" for none."""
    return str(card.meta.get("correspondent") or "").strip()


def document_kind(card: Card) -> str:
    """The kind a document names in `document_type` (`Receipt`), "" for none."""
    return str(card.meta.get("document_type") or "").strip()


def own_date(card: Card) -> str:
    """The date the card itself names in `date` (`2026-10-20`), "" for none.

    Not `Card.day`: for a card without a date that is the day it last
    changed, which says when it was written, not when it happens.
    """
    return str(card.meta.get("date") or "")[:10]


def bucket_of(card: Card) -> str:
    """The first folder of the card's path: `family`, or a person's id."""
    return card.path.partition("/")[0]


def own_folder(graph: Graph, card: Card) -> str:
    """The id of the person whose own folder holds `card` (`homer` for
    `homer/bowling/topic.md`), "" for a card in the family's folder.

    A person's own folder is named by their id, and every person has a
    person card, so the person cards say which folders are someone's.
    """
    bucket = bucket_of(card)
    return bucket if any(p.type == "person" and person_slug(p) == bucket
                         for p in graph.cards.values()) else ""

