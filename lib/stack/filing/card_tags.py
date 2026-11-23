"""A capture card's tags: a topic room's ids first, then the model's keywords and the topics it chose.

    classification = {"tags": ["lake"], "topics": ["camper van", "Gas"]}
    with_room_topic(classification, ["schule"])      # tags: ['schule', 'lake']
    with_chosen_topics(classification, vocabulary)   # tags: ['schule', 'lake', 'van', 'utility']
    tag_list(classification)                         # ['schule', 'lake', 'van', 'utility']

The model answers keywords (`tags`) and, in a key of its own, the topics
of the card's place it picked from a list (`topics`,
`stack.filing.topic_list`). A topic room is the source of truth for its
topic, so its id comes first and is written as the tag rule writes it,
never through a synonym: a room "Schule" keeps `schule`. A topic word
that names no topic of the card's place is left out.

`tag_list` is what the card's `tags:` are written from: the strings of
the answer, stripped. No `Person: X` tags: `persons` says who a capture
is about (ADR-017 §4).

The filing core (`stack.filing.file`) runs these on every capture but an
email's. Pure: no I/O.
"""

from __future__ import annotations

from .topic_list import chosen
from .vocabulary import Vocabulary

__all__ = ["tag_list", "with_chosen_topics", "with_room_topic"]


def with_room_topic(classification: dict, seed_topics: list[str] | None) -> dict:
    """`classification` with a topic room's ids first in its tags, then the
    model's, each once; the same dict, changed.

    The room is the source of truth for its topic, the classifier only
    advises: a capture in a topic room always carries the room's id first.

        >>> with_room_topic({"tags": ["lake", "schule"]}, ["schule"])["tags"]
        ['schule', 'lake']
    """
    if seeds := _strings(seed_topics):
        classification["tags"] = _each_once([*seeds, *_strings(classification.get("tags"))])
    return classification


def with_chosen_topics(classification: dict, vocabulary: Vocabulary) -> dict:
    """`classification` with the ids of the topics the model chose
    (`topics`) after its tags; the same dict, changed, without `topics`.

    `vocabulary` is the card's place's (`stack.filing.topic_list.chosen`); a word that
    names no topic there is left out.

        classification = {"tags": ["lake"], "topics": ["camper van", "Mars"]}
        with_chosen_topics(classification, family)["tags"]     # ['lake', 'van']
    """
    picked = chosen(vocabulary, _strings(classification.pop("topics", None)))
    classification["tags"] = _each_once([*_strings(classification.get("tags")), *picked])
    return classification


def tag_list(classification: dict) -> list[str]:
    """The card's `tags:`, from the answer's free-form tags: strings only,
    stripped, a lone string a list of one.

        >>> tag_list({"tags": [" AI ", 3, "", "Productivity"]})
        ['AI', 'Productivity']
        >>> tag_list({"tags": "AI"})
        ['AI']
    """
    raw_tags = classification.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    return [t.strip() for t in raw_tags if isinstance(t, str) and t.strip()]


def _strings(value) -> list[str]:
    """A list of the non-empty strings in `value`, stripped; a lone string is a list of one."""
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return [v.strip() for v in values if isinstance(v, str) and v.strip()]


def _each_once(tags: list[str]) -> list[str]:
    return list(dict.fromkeys(tags))
