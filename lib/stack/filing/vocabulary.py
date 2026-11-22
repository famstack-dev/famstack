"""The vocabulary of a place in the memory: the topics a card filed there may carry.

    vocabulary = Vocabulary.of("family/notes", vault, seed=SEED, shared_bucket="family")
    vocabulary.topics(["Camper Van", "Homers 40th", "Person: Homer"])
                                # ['van', 'homers-40th']: no private topic of Marge's
    Vocabulary.of("marge", vault, seed=SEED).topics(["Homers 40th"])
                                # ['surprise-party']: Marge's own topic, in her bucket

A household names its topics with topic cards (`<bucket>/<topic>/topic.md`,
`stack.household_topics`). A topic card in a person's bucket is that
person's alone: Marge's "Homers 40th" is a surprise for Homer. A writer
that tagged every card with every bucket's topics would put her topic's id
on Homer's note as soon as the model said "Homers 40th", and the card,
its page and its search label would show it to him.

So a card's topics are read for its place, never for a person: the
vocabulary of a folder is the ontology and the topic cards that everyone
who may read the folder may read (`stack.cards.audience.readable`). The
folder's readers are its bucket's: a person's bucket is read by that
person, the shared bucket by everyone, which as an audience is the empty
set and reads only the shared topic cards. A title the vocabulary does not
hold is a plain keyword (`homers-40th`), never another bucket's topic id.

    folder              readers        topic cards it holds
    family/notes        everyone       family/*
    marge/notes         {marge}        family/*, marge/*
    homer               {homer}        family/*, homer/*

The same vocabulary serves every step that tags one card: the archivist's
write and its corrections, `stack memory reprocess`, the curator's check of
a written card and the card editor. A reply in a room, and the capture
event it carries, show a card's tags only as far as the room's vocabulary
holds them (`RoomView`, `Vocabulary.shown`).

Pure file reads, stdlib only, like `stack.household_topics`: the archivist
and the curator, each in its own container, import it. The admin's view
over every bucket stays `stack.household_topics.get_ontology`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..cards.audience import readable
from ..cards.links import without_person_tags
from ..household_topics import load_ontology_from_vault, topic_cards
from ..ontology import Ontology
from ..tags import card_tags, tag_for, with_declared_topics
from ..vault import DEFAULT_SHARED_BUCKET

__all__ = ["RoomView", "Vocabulary"]


@dataclass(frozen=True)
class Vocabulary:
    """The topics a card at one place may carry: the ontology's and the
    readable topic cards'. Build it with `Vocabulary.of`.

        family = Vocabulary.of("family/notes", vault, seed=SEED)
        family.topics(["Camper Van", "LLMs"])          # ['van', 'llms']
        family.ontology.topic_ids                      # what the card check allows
    """

    ontology: Ontology

    @classmethod
    def of(cls, folder: str, vault: Optional[Path], *, seed: Path,
           shared_bucket: str = DEFAULT_SHARED_BUCKET) -> "Vocabulary":
        """The vocabulary of `folder`, a vault path, a folder or a bare bucket.

        Reads the vault's ontology (else the shipped `seed`) and its topic
        cards, and keeps the topic cards everyone who may read `folder` may
        read. No vault (`None`) gives the seed alone. Only the first folder
        of `folder` counts, so the bucket is enough before the card's path is
        known: `"marge"`, `"marge/notes"` and `"marge/notes/2026/11/cake.md"`
        have one vocabulary.

            Vocabulary.of("family", vault, seed=SEED).topics(["Homers 40th"])   # ['homers-40th']
            Vocabulary.of("marge", vault, seed=SEED).topics(["Homers 40th"])    # ['surprise-party']
        """
        if vault is None:
            return cls(Ontology.load(seed))
        ontology = load_ontology_from_vault(vault) or Ontology.load(seed)
        readers = _readers(folder, shared_bucket)
        titles: dict[str, str] = {}
        for card in topic_cards(vault, shared_bucket=shared_bucket):
            if readable(card.path, readers, shared_bucket=shared_bucket):
                titles.setdefault(card.id, card.title)
        return cls(with_declared_topics(ontology, titles))

    def topics(self, raw: Iterable[str]) -> list[str]:
        """The tags a card here is written with for the model's `raw` tags:
        topics by id, the rest as keywords, each once, no `Person:` tags
        (ADR-017 §4, `stack.tags.card_tags`).

            family.topics(["Camper Van", "Homers 40th", "Person: Homer", "2026"])
                                                       # ['van', 'homers-40th']
        """
        return card_tags(without_person_tags(raw), self.ontology)

    def shown(self, tags: Iterable[str], *, card: "Vocabulary") -> list[str]:
        """The tags of a card written with the vocabulary `card` that this
        vocabulary's readers may see, in their order.

        A tag is left out when `card` reads it as a topic this vocabulary
        does not hold: a room shows a card's own topic only when everyone
        in the room may read it. Keywords and shared topics stay.

            homers = Vocabulary.of("homer", vault, seed=SEED)
            family.shown(["Garage Project", "Camper Van", "llms"], card=homers)
                                                       # ['Camper Van', 'llms']
        """
        # SAFETY: a person's topic on their own card stays off a reply in a
        # room that other people read, also when that person sent it there.
        return [tag for tag in tags if self._holds_topic_of(str(tag), card)]

    def _holds_topic_of(self, tag: str, card: "Vocabulary") -> bool:
        """Whether `tag`, read by `card`, names no topic outside this vocabulary."""
        topic = tag_for(tag, card.ontology)
        if topic is None or card.ontology.topic(topic) is None:
            return True
        return self.ontology.topic(topic) is not None


@dataclass(frozen=True)
class RoomView:
    """What a room may read of one card's tags: the room's vocabulary and
    the card's. A room of several people has the shared vocabulary, a
    person's room with the archivist that person's.

        view = RoomView(room=family, card=marges)
        view.of({"title": "Cake", "tags": ["Homers 40th", "Camper Van"]})
                                    # {'title': 'Cake', 'tags': ['Camper Van']}
    """

    room: Vocabulary
    card: Vocabulary

    @classmethod
    def between(cls, places, card_path: str, *, room: str) -> "RoomView":
        """The view of the card at `card_path` from a room whose bucket is
        `room`. `places` gives a folder's vocabulary (`places.vocabulary(folder)`,
        as the archivist's vault context does).

        The room's bucket is its one person's, or the shared bucket when it
        has more than one person or is not known, so a reply never shows
        more than the shared topics where someone else could read it.

            RoomView.between(vault, "marge/notes/2026/11/cake.md", room="family")
        """
        return cls(room=places.vocabulary(room), card=places.vocabulary(card_path))

    def of(self, view: dict) -> dict:
        """`view`, a classification as a reply shows it, with only the tags
        the room may read; a new dict, `view` is not changed."""
        # SAFETY: a topic of the card's bucket that the room's people may not
        # all read stays on the card and off the reply.
        return {**view, "tags": self.room.shown(view.get("tags") or [], card=self.card)}

    def of_changes(self, changes):
        """A correction's changes (each with `field` and `value`) without a
        tag the room may not read; None (the writer did not say) stays None."""
        if changes is None:
            return None
        return [c for c in changes
                if c.field != "tag" or self.room.shown([c.value], card=self.card)]

    def event(self, envelope: dict) -> dict:
        """A `dev.famstack.event` envelope as the room may receive it: its
        `data` with only the tags the room may read; a new dict.

            view.event({"type": "capture.filed", "data": {"tags": ["Homers 40th"]}})
                                    # {'type': 'capture.filed', 'data': {'tags': []}}
        """
        # SAFETY: every client and bot in the room reads the event, not only
        # the reply's text, so it carries no more than the text shows.
        data = envelope.get("data")
        if not isinstance(data, dict) or "tags" not in data:
            return envelope
        return {**envelope, "data": self.of(data)}


def _readers(folder: str, shared_bucket: str) -> frozenset[str]:
    """Who reads `folder`: its bucket's person, or everyone (the empty set)
    for the shared bucket and the vault's top level.

    >>> sorted(_readers("marge/notes/2026/11/cake.md", "family"))
    ['marge']
    >>> _readers("family/van", "family")
    frozenset()
    """
    bucket = folder.strip("/").partition("/")[0]
    return frozenset() if bucket in ("", shared_bucket) else frozenset({bucket})
