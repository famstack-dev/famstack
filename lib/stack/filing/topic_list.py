"""The list of topics that a model chooses from, and the reading of its choice as topic ids.

    names = topics_to_choose(vocab, "en")    # ['Garden', 'Bike Trip', 'Health', ...]
    # ... the model answers {"topics": ["garden", "Gesundheit", "Mars"]}
    chosen(vocab, ["garden", "Gesundheit", "Mars"])    # ['garden', 'health']

A note and a diary entry are shown the topics of the place of the card (`stack.filing.vocabulary.Vocabulary`).
They answer with the topics that they are about.
This module builds the list and reads the answer back.

The list holds the topic cards first.
The topic cards of the own bucket of the place come before the shared ones.
The topics of the ontology follow.
The list holds at most `TOPICS_SHOWN` names.
The 42 topics of the shipped ontology therefore never push the topic cards out.
Each name is cut at `TOPIC_NAME_CHARS`.

The reading of the answer uses more than the keyword rule (`Vocabulary.topics`),
because the model picked each word from a list of topics:

    the word of the model     read as                 why
    "garden", "Bike Trip"     garden, bike-trip       the id or the title of a topic card
    "Gesundheit"              health                  the name of a topic, in any language
    "cycling-trip"            bike-trip               the former id of a renamed topic
    "holiday"                 travel                  a synonym of the ontology, last
    "Schule"                  kita                    a topic card titled "Schule"
                                                      wins over the synonym
    "Mars", "Invoice"         (left out)              no topic, or a document type

Here a synonym is a hint only.
On a free keyword it would turn a note about camping gas into `utility`.
`Vocabulary.topics` therefore never uses a synonym.

The module uses only the standard library and has no side effects.
The archivist and the diary import it, each in its own container.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from ..ontology import Ontology, Topic
from ..tags import tag_for
from .vocabulary import Vocabulary

__all__ = ["TOPICS_SHOWN", "TOPIC_NAME_CHARS", "chosen", "prompt_section", "topics_to_choose"]

# How many topics a prompt lists, and how long one name may be there.
# Both limits keep the prompt short enough for a small model on a 16 GB Mac.
# The length limit also keeps a long title that a person wrote from carrying a paragraph into the prompt.
TOPICS_SHOWN = 60
TOPIC_NAME_CHARS = 80


def topics_to_choose(vocabulary: Vocabulary, language: str) -> list[str]:
    """The names of the topics that a model is shown to choose from, in `language`, each once.

    The order is: the topic cards of the own bucket of the place, the shared topic cards, the topics of the ontology.
    The list holds at most `TOPICS_SHOWN` names, and each name is cut at `TOPIC_NAME_CHARS`.

    The list shows a title longer than `TOPIC_NAME_CHARS` cut.
    The cut name does not read back as its topic (`chosen`).
    Such a topic can be chosen only by its id.
    Titles are short, and the cut protects the prompt.

        topics_to_choose(Vocabulary.of("alex", vault, seed=SEED), "de")[:3]
        # ['Surprise', 'Garden', 'Bike Trip']
    """
    ontology = vocabulary.ontology
    topics = [*(ontology.declared_topics[i] for i in vocabulary.topic_cards), *ontology.topics.values()]
    names = (_cut(topic.name(language)) for topic in topics)
    return list(dict.fromkeys(name for name in names if name))[:TOPICS_SHOWN]


def prompt_section(vocabulary: Vocabulary, language: str) -> str:
    """The vocabulary that a classifier is offered as prompt text.

    The text holds the topics of the ontology with their synonyms and its document types
    (`Ontology.classifier_prompt_section`).
    The titles of the topic cards of the place follow as a JSON list.

        Topics (pick the best match; synonyms in parentheses):
          - Health
        ...
        The memory's own topics: ["Garden", "Bike Trip"]
    """
    ontology = vocabulary.ontology
    section = ontology.classifier_prompt_section(language)
    names = [_cut(ontology.declared_topics[i].name(language)) for i in vocabulary.topic_cards]
    if not names:
        return section
    return f"{section}\n\nThe memory's own topics: {json.dumps(names, ensure_ascii=False)}"


def chosen(vocabulary: Vocabulary, raw: Iterable) -> list[str]:
    """The ids of the topics that the model chose from its list, in its order, each once.

    The function leaves out a word that names no topic of `vocabulary`.
    It also leaves out anything that is not a string.

    A topic is named by its id or its name in any language.
    A topic card is named by its id or its title.
    A renamed topic is named by a former id.
    A topic of the ontology is named, last, by a synonym in any language.

        chosen(vocab, ["garden", "Gesundheit", "Gas", "Mars", 3])
        # ['garden', 'health', 'utility']
    """
    found = (_topic_named(vocabulary, text) for text in raw if isinstance(text, str))
    return list(dict.fromkeys(topic for topic in found if topic))


# ── Reading one word ─────────────────────────────────────────────────────

def _topic_named(vocabulary: Vocabulary, text: str) -> str | None:
    """The id of the topic `text` names in `vocabulary` (see `chosen`), or None."""
    ontology = vocabulary.ontology
    tag = vocabulary.current(tag_for(text, ontology) or "")
    if ontology.topic(tag) is not None:
        return tag
    synonym = _by_synonym(text, ontology)
    return synonym.id if synonym else None


def _by_synonym(text: str, ontology: Ontology) -> Topic | None:
    """The ontology topic with `text` as a name or synonym in any language."""
    for language in ontology.languages or ["en"]:
        if topic := ontology.resolve_topic(text, language):
            return topic
    return None


def _cut(name: str) -> str:
    """`name` on one line, at most `TOPIC_NAME_CHARS` long."""
    return " ".join(name.split())[:TOPIC_NAME_CHARS].strip()
