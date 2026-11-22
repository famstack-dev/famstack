"""The list of topics a model chooses from, and reading its choice back as topic ids.

    names = topics_to_choose(family, "en")    # ['Camper Van', 'Quilt for Lisa', 'Health', ...]
    # ... the model answers {"topics": ["camper van", "Gesundheit", "Mars"]}
    chosen(family, ["camper van", "Gesundheit", "Mars"])    # ['van', 'health']

A note and a diary entry are shown the topics of the card's place
(`stack.filing.vocabulary.Vocabulary`) and answer the ones they are about.
This module builds that list and reads the answer back.

The list puts the household's topic cards first, the place's own bucket
before the shared one, then the ontology's topics. It holds at most
`TOPICS_SHOWN` names, so the 42 topics of the shipped ontology never push
a household's own topics out, and each name is cut at `TOPIC_NAME_CHARS`.

Reading the answer back uses more than the keyword rule
(`Vocabulary.topics`), because a word in it was picked from a list of
topics:

    the model's word          read as                 why
    "van", "Camper Van"       van                     a topic card's id or title
    "Gesundheit"              health                  a topic's name, any language
    "quilt-for-lisa"          lisas-quilt             a renamed topic's former id
    "holiday"                 travel                  an ontology synonym, last
    "Schule"                  kita                    a topic card titled "Schule"
                                                      wins over the synonym
    "Mars", "Invoice"         (left out)              no topic, or a document type

A synonym is a hint only here. On a free keyword it would turn a
camping-gas note into `utility`, so `Vocabulary.topics` never uses one.

Pure, stdlib only, like `stack.filing.vocabulary`: the archivist and the
diary, each in its own container, import it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from ..ontology import Ontology, Topic
from ..tags import tag_for
from .vocabulary import Vocabulary

__all__ = ["TOPICS_SHOWN", "TOPIC_NAME_CHARS", "chosen", "prompt_section", "topics_to_choose"]

# How many topics a prompt lists, and how long one name may be there. Both
# keep the prompt short enough for a small model on a 16 GB Mac, and the
# length limit keeps a long title, which a family member wrote, from
# carrying a paragraph into the prompt.
TOPICS_SHOWN = 60
TOPIC_NAME_CHARS = 80


def topics_to_choose(vocabulary: Vocabulary, language: str) -> list[str]:
    """The names of the topics a model is shown to choose from: the
    place's own topic cards, then the shared ones, then the ontology's,
    in `language`, each once. At most `TOPICS_SHOWN`, each cut at
    `TOPIC_NAME_CHARS`.

    A title longer than `TOPIC_NAME_CHARS` is listed cut, and the cut name
    does not read back as its topic (`chosen`): such a topic can be chosen
    only by its id. Family titles are short; the cut protects the prompt.

        topics_to_choose(Vocabulary.of("marge", vault, seed=SEED), "de")[:3]
        # ['Homers 40th', 'Camper Van', 'Quilt for Lisa']
    """
    ontology = vocabulary.ontology
    topics = [*(ontology.declared_topics[i] for i in vocabulary.topic_cards), *ontology.topics.values()]
    names = (_cut(topic.name(language)) for topic in topics)
    return list(dict.fromkeys(name for name in names if name))[:TOPICS_SHOWN]


def prompt_section(vocabulary: Vocabulary, language: str) -> str:
    """The vocabulary a classifier is offered as prompt text: the
    ontology's topics with their synonyms and its document types
    (`Ontology.classifier_prompt_section`), then the place's topic cards'
    titles as a JSON list.

        Topics (pick the best match; synonyms in parentheses):
          - Health
        ...
        The household's own topics: ["Camper Van", "Quilt for Lisa"]
    """
    ontology = vocabulary.ontology
    section = ontology.classifier_prompt_section(language)
    names = [_cut(ontology.declared_topics[i].name(language)) for i in vocabulary.topic_cards]
    if not names:
        return section
    return f"{section}\n\nThe household's own topics: {json.dumps(names, ensure_ascii=False)}"


def chosen(vocabulary: Vocabulary, raw: Iterable) -> list[str]:
    """The ids of the topics the model chose from its list, in its order,
    each once; a word that names no topic of `vocabulary` is left out, and
    so is anything that is not a string.

    A topic is named by its id or name in any language, a topic card by
    its id or title, a renamed topic by a former id, and an ontology
    topic, last, by a synonym in any language.

        chosen(family, ["camper van", "Gesundheit", "Gas", "Mars", 3])
        # ['van', 'health', 'utility']
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
