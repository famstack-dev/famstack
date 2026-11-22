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
Nor is the id itself a keyword there (`withheld`): search reads every
bucket's topics, so a family card tagged `surprise-party` would be listed
under Marge's title. `topics` leaves such an id out, and the curator's
check refuses it when a write adds it; a card that already carries it
keeps it, so a keyword that later became someone's topic id does not lock
the card.

Debt: every person's topic id is a reserved word for everyone else's new
cards. A keyword that equals another bucket's topic id (`camping`, once
Marge has `marge/camping/`) is left off a new family card without a word,
and folder names are ordinary words. Trigger: the first report of a lost
keyword, or a refused edit of a card. A reserved form for private topic
ids would remove it.

    folder              readers        topic cards it holds
    family/notes        everyone       family/*
    marge/notes         {marge}        family/*, marge/*
    homer               {homer}        family/*, homer/*

The same vocabulary serves every step that tags one card: the model's
prompt, the archivist's write and its corrections, `stack memory
reprocess`, the diary, the curator's check of a written card and the card
editor. A reply in a room, and the capture event it carries, show a card's
tags only as far as the room's vocabulary holds them (`RoomView`,
`Vocabulary.shown`), the topics by their names.

A keyword becomes a tag by `topics`: a topic by its id or name, the rest
as a keyword, never through a synonym ("Gas" stays `gas`). A topic the
model chose from a list is read by `stack.filing.topic_list.chosen`,
which also reads synonyms. A topic's former id (`former_topic_ids`, after
`stack memory topic rename`) is read as the topic's id by both.

Pure file reads, stdlib only, like `stack.household_topics`: the archivist,
the diary and the curator, each in its own container, import it. The
admin's view over every bucket stays `stack.household_topics.get_ontology`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

from ..cards.audience import readable
from ..cards.links import without_person_tags
from ..household_topics import TopicCard, load_ontology_from_vault, topic_cards
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
        family.labels(["van", "llms"], "en")           # ['Camper Van', 'llms']
        family.ontology.topic_ids                      # what the card check allows

    `topic_cards` are the ids of the readable topic cards, the place's own
    bucket's first; `renamed` maps a former topic id to the id it has now;
    `withheld` are the ids of the topic cards this place may not read, which
    no tag here may be.
    """

    ontology: Ontology
    topic_cards: tuple[str, ...] = ()
    renamed: Mapping[str, str] = field(default_factory=dict)
    withheld: frozenset[str] = frozenset()

    @classmethod
    def of(cls, folder: str, vault: Optional[Path], *, seed: Path,
           shared_bucket: str = DEFAULT_SHARED_BUCKET, read_in: str | None = None) -> "Vocabulary":
        """The vocabulary of `folder`, a vault path, a folder or a bare bucket.

        Reads the vault's ontology (else the shipped `seed`) and its topic
        cards, and keeps the topic cards everyone who may read `folder` may
        read. No vault (`None`) gives the seed alone. Only the first folder
        of `folder` counts, so the bucket is enough before the card's path is
        known: `"marge"`, `"marge/notes"` and `"marge/notes/2026/11/cake.md"`
        have one vocabulary.

        `read_in` is the bucket of a room whose people also read what is
        written with the vocabulary, such as a model's summary in a reply:
        a topic card is then kept only when the room's people may read it
        too. A note Marge sends to #family files into her bucket, but the
        model writing it is shown only the family's topics. A family card
        sent from Marge's own room is shown the family's topics only, not
        hers: offering fewer topics is the safe side.

            Vocabulary.of("family", vault, seed=SEED).topics(["Homers 40th"])   # ['homers-40th']
            Vocabulary.of("marge", vault, seed=SEED).topics(["Homers 40th"])    # ['surprise-party']
            Vocabulary.of("marge", vault, seed=SEED, read_in="family").topics(["Homers 40th"])
                                                                                # ['homers-40th']
        """
        if vault is None:
            return cls(Ontology.load(seed))
        ontology = load_ontology_from_vault(vault) or Ontology.load(seed)
        readers = _readers(folder, shared_bucket)
        if read_in is not None:
            # SAFETY: a room's people read what the model writes; a topic
            # card is offered only when they may read it too.
            room = _readers(read_in, shared_bucket)
            readers = room | readers if room and readers else frozenset()
        every_card = topic_cards(vault, shared_bucket=shared_bucket)
        cards = sorted((card for card in every_card
                        if readable(card.path, readers, shared_bucket=shared_bucket)),
                       key=lambda card: _bucket(card.path) == shared_bucket)
        titles: dict[str, str] = {}
        for card in cards:
            titles.setdefault(card.id, card.title)
        ontology = with_declared_topics(ontology, titles)
        withheld = frozenset(card.id for card in every_card) - set(ontology.topic_ids)
        return cls(ontology, tuple(i for i in titles if i in ontology.declared_topics),
                   _renamed(cards, ontology), withheld)

    def topics(self, raw: Iterable[str]) -> list[str]:
        """The tags a card here is written with for the model's `raw` tags:
        topics by id or name, the rest as keywords, each once, no `Person:`
        tags (ADR-017 §4, `stack.tags.card_tags`). No synonyms: a keyword
        stays a keyword. A renamed topic's former id becomes its id. The id
        of a topic this place may not read is left out (`withheld`).

            family.topics(["Camper Van", "Homers 40th", "Person: Homer", "2026"])
                                                       # ['van', 'homers-40th']
            family.topics(["Gas", "quilt-for-lisa"])   # ['gas', 'lisas-quilt'] after a rename
            family.topics(["surprise-party", "camping"])   # ['camping']
        """
        written = card_tags(without_person_tags(raw), self.ontology)
        # SAFETY: another bucket's topic id on this card would show the card
        # under that topic's title wherever every bucket's topics are read.
        return list(dict.fromkeys(self.current(tag) for tag in written
                                  if self.current(tag) not in self.withheld))

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

    def labels(self, tags: Iterable[str], language: str) -> list[str]:
        """`tags` as a person reads them: a topic by its name in `language`
        (a topic card by its title), a keyword as it is written; each once.

            family.labels(["van", "Camper Van", "llms", "health"], "en")
                                                       # ['Camper Van', 'llms', 'Health']
        """
        return list(dict.fromkeys(self._label(str(tag), language) for tag in tags))

    def current(self, tag: str) -> str:
        """`tag`, or the id a renamed topic has now when `tag` is a former id of it.

            family.current("quilt-for-lisa")    # 'lisas-quilt' after the rename
            family.current("llms")              # 'llms'
        """
        return tag if self.ontology.topic(tag) is not None else self.renamed.get(tag, tag)

    # ── Reading one tag ──────────────────────────────────────────────────

    def _label(self, tag: str, language: str) -> str:
        topic = self.ontology.topic(self.current(tag_for(tag, self.ontology) or ""))
        return topic.name(language) if topic else tag

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
    person's room with the archivist that person's. A reply names a topic
    by its name in `language`, the event keeps the tags as they are.

        view = RoomView(room=family, card=marges, language="en")
        view.of({"title": "Cake", "tags": ["Homers 40th", "van"]})
                                    # {'title': 'Cake', 'tags': ['Camper Van']}
    """

    room: Vocabulary
    card: Vocabulary
    language: str = "en"

    @classmethod
    def between(cls, places, card_path: str, *, room: str, language: str = "en") -> "RoomView":
        """The view of the card at `card_path` from a room whose bucket is
        `room`, naming topics in `language`. `places` gives a folder's
        vocabulary (`places.vocabulary(folder)`, as the archivist's vault
        context does).

        The room's bucket is its one person's, or the shared bucket when it
        has more than one person or is not known, so a reply never shows
        more than the shared topics where someone else could read it.

            RoomView.between(vault, "marge/notes/2026/11/cake.md", room="family", language="en")
        """
        return cls(room=places.vocabulary(room), card=places.vocabulary(card_path), language=language)

    def of(self, view: dict) -> dict:
        """`view`, a classification as a reply shows it, with only the tags
        the room may read, a topic by its name; a new dict, `view` is not
        changed."""
        # SAFETY: a topic of the card's bucket that the room's people may not
        # all read stays on the card and off the reply.
        shown = self.room.shown(view.get("tags") or [], card=self.card)
        return {**view, "tags": self.room.labels(shown, self.language)}

    def of_changes(self, changes):
        """A correction's changes (dataclasses with `field` and `value`)
        without a tag the room may not read, a topic by its name; None (the
        writer did not say) stays None.

            view.of_changes([Change(field="tag", kind="added", value="van")])
            # [Change(field='tag', kind='added', value='Camper Van')]
        """
        if changes is None:
            return None
        return [self._labelled(c) for c in changes
                if c.field != "tag" or self.room.shown([c.value], card=self.card)]

    def _labelled(self, change):
        """`change` with a tag's value as the room reads it (`Vocabulary.labels`)."""
        if change.field != "tag":
            return change
        return replace(change, value=self.room.labels([change.value], self.language)[0])

    def event(self, envelope: dict) -> dict:
        """A `dev.famstack.event` envelope as the room may receive it: its
        `data` with only the tags the room may read, as they are; a new dict.

            view.event({"type": "capture.filed", "data": {"tags": ["Homers 40th"]}})
                                    # {'type': 'capture.filed', 'data': {'tags': []}}
        """
        # SAFETY: every client and bot in the room reads the event, not only
        # the reply's text, so it carries no more than the text shows.
        data = envelope.get("data")
        if not isinstance(data, dict) or "tags" not in data:
            return envelope
        return {**envelope, "data": {**data, "tags": self.room.shown(data["tags"] or [], card=self.card)}}


# ── Helpers ──────────────────────────────────────────────────────────────

def _readers(folder: str, shared_bucket: str) -> frozenset[str]:
    """Who reads `folder`: its bucket's person, or everyone (the empty set)
    for the shared bucket and the vault's top level.

    >>> sorted(_readers("marge/notes/2026/11/cake.md", "family"))
    ['marge']
    >>> _readers("family/van", "family")
    frozenset()
    """
    bucket = _bucket(folder)
    return frozenset() if bucket in ("", shared_bucket) else frozenset({bucket})


def _bucket(path: str) -> str:
    """The first folder of `path`, a vault path, a folder or a bare bucket:
    `"marge/notes/2026/11/cake.md"` and `"marge"` give `"marge"`."""
    return path.strip("/").partition("/")[0]


def _renamed(cards: list[TopicCard], ontology: Ontology) -> dict[str, str]:
    """Each former id of a readable topic card to the id the card has now.

    A former id that is a topic again (a new card took the old name) stays
    that topic's, and the first card to list an id keeps it.
    """
    renamed: dict[str, str] = {}
    for card in cards:
        for former in card.former:
            if ontology.topic(former) is None and ontology.topic(card.id) is not None:
                renamed.setdefault(former, card.id)
    return renamed
