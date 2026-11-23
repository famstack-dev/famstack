"""The vocabulary of a place in the memory: the topics that a card filed there may carry.

    vocab = Vocabulary.of("shared/notes", vault, seed=SEED, shared_bucket="shared")
    vocab.topics(["Garden", "Sam's 40th", "Person: Sam"])
                                # ['garden', 'sams-40th']: no private topic of alex
    Vocabulary.of("alex", vault, seed=SEED).topics(["Sam's 40th"])
                                # ['surprise']: the own topic of alex, in the bucket of alex

Topic cards (`<bucket>/<topic>/topic.md`, `stack.topics`) name the topics.
A topic card in the bucket of a person belongs to that person alone.
Take the topic "Sam's 40th" of alex, a surprise for sam.
A writer that tagged every card with the topics of every bucket would put the id of that topic on the note of sam.
This happens as soon as the model says "Sam's 40th".
The card, its page and its search label would then show the topic to sam.

The topics of a card are therefore read for its place, never for a person.
The vocabulary of a folder is the ontology plus the topic cards that everyone who may read the folder may read
(`stack.cards.audience.readable`).

The readers of a folder are the readers of its bucket.
The person of a bucket reads that bucket.
Everyone reads the shared bucket.
As an audience, everyone is the empty set.
That audience reads only the shared topic cards.

A title that the vocabulary does not hold is a plain keyword (`sams-40th`).
It is never the topic id of another bucket.

The id itself is not a keyword there either (`withheld`).
The search reads the topics of every bucket.
A shared card tagged `surprise` would then be listed under the private title of alex.
`topics` leaves such an id out, and the check of the curator refuses it when a write adds it.
A card that already carries the id keeps it.
A keyword that later became the topic id of someone therefore does not lock the card.

Debt: the topic id of every person is a reserved word for the new cards of everyone else.
A keyword that equals the topic id of another bucket is left off a new shared card without a word.
An example is `camping`, once alex has `alex/camping/`.
Folder names are ordinary words.
Trigger: the first report of a lost keyword, or a refused edit of a card.
A reserved form for private topic ids would remove the debt.

    folder              readers        topic cards it holds
    shared/notes        everyone       shared/*
    alex/notes          {alex}         shared/*, alex/*
    sam                 {sam}          shared/*, sam/*

The same vocabulary serves every step that tags one card:

- the prompt of the model,
- the write of the archivist and its corrections,
- `stack memory reprocess`,
- the diary,
- the check of the curator for a written card,
- the card editor.

A reply in a room shows the tags of a card only as far as the vocabulary of the room holds them (`RoomView`, `Vocabulary.shown`).
It shows the topics by their names.
The capture event of the reply follows the same rule.

`topics` turns a keyword into a tag.
A topic becomes its id when the keyword is its id or its name.
Every other keyword stays a keyword.
A synonym never applies ("Gas" stays `gas`).
`stack.filing.topic_list.chosen` reads a topic that the model chose from a list, and it reads synonyms too.
Both read the former id of a topic (`former_topic_ids`, after `stack memory topic rename`) as the id of the topic.

The module reads files and uses only the standard library, like `stack.topics`.
The archivist, the diary and the curator import it, each in its own container.
The admin's view over every bucket stays `stack.topics.get_ontology`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

from ..cards.audience import readable
from ..cards.links import without_person_tags
from ..ontology import Ontology
from ..tags import card_tags, tag_for, with_declared_topics
from ..topics import TopicCard, load_ontology_from_vault, topic_cards
from ..vault import DEFAULT_SHARED_BUCKET

__all__ = ["RoomView", "Vocabulary"]

@dataclass(frozen=True)
class Vocabulary:
    """The topics that a card at one place may carry: the topics of the ontology and the readable topic cards.

    Build it with `Vocabulary.of`.

        shared = Vocabulary.of("shared/notes", vault, seed=SEED)
        shared.topics(["Garden", "LLMs"])              # ['garden', 'llms']
        shared.labels(["garden", "llms"], "en")        # ['Garden', 'llms']
        shared.ontology.topic_ids                      # what the card check allows

    - `topic_cards` holds the ids of the readable topic cards, the cards of the own bucket of the place first.
    - `renamed` maps the former id of a topic to its current id.
    - `withheld` holds the ids of the topic cards that this place may not read. No tag here may be one of them.
    """

    ontology: Ontology
    topic_cards: tuple[str, ...] = ()
    renamed: Mapping[str, str] = field(default_factory=dict)
    withheld: frozenset[str] = frozenset()

    @classmethod
    def of(cls, folder: str, vault: Optional[Path], *, seed: Path,
           shared_bucket: str = DEFAULT_SHARED_BUCKET, read_in: str | None = None) -> "Vocabulary":
        """The vocabulary of `folder`, which is a vault path, a folder or a bare bucket.

        The function reads the ontology of the vault, else the shipped `seed`, and the topic cards of the vault.
        It keeps the topic cards that everyone who may read `folder` may read.
        With no vault (`None`), it returns the seed alone.
        Only the first folder of `folder` counts.
        The bucket is therefore enough before the path of the card is known.
        `"alex"`, `"alex/notes"` and `"alex/notes/2026/11/cake.md"` have one vocabulary.

        `read_in` is the bucket of a room whose people also read what the writer writes with the vocabulary.
        An example is the summary of a model in a reply.
        A topic card is then kept only when the people of the room may read it too.
        A note that alex sends to a shared room files into the bucket of alex.
        The model that writes it is shown only the shared topics.
        A shared card that is sent from the own room of alex is shown the shared topics only, not the topics of alex.
        Offering fewer topics is the safe side.

            Vocabulary.of("shared", vault, seed=SEED).topics(["Sam's 40th"])    # ['sams-40th']
            Vocabulary.of("alex", vault, seed=SEED).topics(["Sam's 40th"])      # ['surprise']
            Vocabulary.of("alex", vault, seed=SEED, read_in="shared").topics(["Sam's 40th"])
                                                                                # ['sams-40th']
        """
        if vault is None:
            return cls(Ontology.load(seed))
        ontology = load_ontology_from_vault(vault) or Ontology.load(seed)
        readers = _readers(folder, shared_bucket)
        if read_in is not None:
            # SAFETY: the people of a room read what the model writes.
            # A topic card is offered only when they may read it too.
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
        """The tags that a card here is written with, for the `raw` tags of the model, each once.

        A topic becomes its id when the tag is its id or its name.
        Every other tag stays a keyword.
        The result holds no `Person:` tag (ADR-017 §4, `stack.tags.card_tags`).
        A synonym never applies, so a keyword stays a keyword.
        The former id of a renamed topic becomes its current id.
        The function leaves out the id of a topic that this place may not read (`withheld`).

            shared.topics(["Garden", "Sam's 40th", "Person: Sam", "2026"])
                                                       # ['garden', 'sams-40th']
            shared.topics(["Gas", "cycling-trip"])     # ['gas', 'bike-trip'] after a rename
            shared.topics(["surprise", "camping"])     # ['camping']
        """
        written = card_tags(without_person_tags(raw), self.ontology)
        # SAFETY: the topic id of another bucket on this card would show the card
        # under the title of that topic wherever the topics of every bucket are read.
        return list(dict.fromkeys(self.current(tag) for tag in written
                                  if self.current(tag) not in self.withheld))

    def shown(self, tags: Iterable[str], *, card: "Vocabulary") -> list[str]:
        """The tags of a card that the readers of this vocabulary may see, in their order.

        The card was written with the vocabulary `card`.
        The function leaves out a tag when `card` reads it as a topic that this vocabulary does not hold.
        A room therefore shows a topic of the card only when everyone in the room may read it.
        Keywords and shared topics stay.

            sams = Vocabulary.of("sam", vault, seed=SEED)
            shared.shown(["Workshop", "Garden", "llms"], card=sams)
                                                       # ['Garden', 'llms']
        """
        # SAFETY: a topic of a person on their own card stays off a reply in a
        # room that other people read, also when that person sent it there.
        return [tag for tag in tags if self._holds_topic_of(str(tag), card)]

    def labels(self, tags: Iterable[str], language: str) -> list[str]:
        """`tags` as a person reads them, each once.

        A topic shows its name in `language`, and a topic card shows its title.
        A keyword shows as it is written.

            shared.labels(["garden", "Garden", "llms", "health"], "en")
                                                       # ['Garden', 'llms', 'Health']
        """
        return list(dict.fromkeys(self._label(str(tag), language) for tag in tags))

    def current(self, tag: str) -> str:
        """`tag`, or the current id of a renamed topic when `tag` is a former id of it.

            shared.current("cycling-trip")      # 'bike-trip' after the rename
            shared.current("llms")              # 'llms'
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
    """What a room may read of the tags of one card: the vocabulary of the room and the vocabulary of the card.

    A room of several people has the shared vocabulary.
    The room of one person with the archivist has the vocabulary of that person.
    A reply names a topic by its name in `language`.
    The event keeps the tags as they are.

        view = RoomView(room=shared, card=alexs, language="en")
        view.of({"title": "Cake", "tags": ["surprise", "garden"]})
                                    # {'title': 'Cake', 'tags': ['Garden']}
    """

    room: Vocabulary
    card: Vocabulary
    language: str = "en"

    @classmethod
    def between(cls, places, card_path: str, *, room: str, language: str = "en") -> "RoomView":
        """The view of the card at `card_path` from a room whose bucket is `room`, with topics named in `language`.

        `places` gives the vocabulary of a folder (`places.vocabulary(folder)`, as the vault context of the archivist does).

        The bucket of a room is the bucket of its one person.
        It is the shared bucket when the room has more than one person or the person is not known.
        A reply therefore never shows more than the shared topics where someone else could read it.

            RoomView.between(vault, "alex/notes/2026/11/cake.md", room="shared", language="en")
        """
        return cls(room=places.vocabulary(room), card=places.vocabulary(card_path), language=language)

    def of(self, view: dict) -> dict:
        """`view`, a classification as a reply shows it, as a new dict that holds only the tags the room may read.

        A topic shows its name. The function does not change `view`.
        """
        # SAFETY: a topic of the bucket of the card that not all people of the room may read
        # stays on the card and off the reply.
        shown = self.room.shown(view.get("tags") or [], card=self.card)
        return {**view, "tags": self.room.labels(shown, self.language)}

    def of_changes(self, changes):
        """The changes of a correction (dataclasses with `field` and `value`) without a tag that the room may not read.

        A topic shows its name.
        `None` (the writer did not say) stays `None`.

            view.of_changes([Change(field="tag", kind="added", value="garden")])
            # [Change(field='tag', kind='added', value='Garden')]
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
        """A `dev.famstack.event` envelope as the room may receive it, as a new dict.

        Its `data` holds only the tags that the room may read, as they are.

            view.event({"type": "capture.filed", "data": {"tags": ["surprise"]}})
                                    # {'type': 'capture.filed', 'data': {'tags': []}}
        """
        # SAFETY: every client and bot in the room reads the event, not only the text of the reply.
        # The event therefore carries no more than the text shows.
        data = envelope.get("data")
        if not isinstance(data, dict) or "tags" not in data:
            return envelope
        return {**envelope, "data": {**data, "tags": self.room.shown(data["tags"] or [], card=self.card)}}


# ── Helpers ──────────────────────────────────────────────────────────────

def _readers(folder: str, shared_bucket: str) -> frozenset[str]:
    """Who reads `folder`: the person of its bucket.

    Everyone (the empty set) reads the shared bucket and the top level of the vault.

    >>> sorted(_readers("alex/notes/2026/11/cake.md", "shared"))
    ['alex']
    >>> _readers("shared/garden", "shared")
    frozenset()
    """
    bucket = _bucket(folder)
    return frozenset() if bucket in ("", shared_bucket) else frozenset({bucket})


def _bucket(path: str) -> str:
    """The first folder of `path`, which is a vault path, a folder or a bare bucket.

    `"alex/notes/2026/11/cake.md"` and `"alex"` give `"alex"`.
    """
    return path.strip("/").partition("/")[0]


def _renamed(cards: list[TopicCard], ontology: Ontology) -> dict[str, str]:
    """Map each former id of a readable topic card to the current id of the card.

    A former id that is a topic again stays the id of that topic, because a new card took the old name.
    The first card that lists an id keeps it.
    """
    renamed: dict[str, str] = {}
    for card in cards:
        for former in card.former:
            if ontology.topic(former) is None and ontology.topic(card.id) is not None:
                renamed.setdefault(former, card.id)
    return renamed
