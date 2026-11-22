"""note_topics: a capture's topics, from the list the model is shown to the card's tags.

A note or a link is shown the topics its card may carry and answers the
ones it is about in a key of its own, beside its keywords:

    hint = tags_hint(["lake"], ["Camper Van", "Health"])   # the prompt's tags hint
    classification = {"tags": ["lake"], "topics": ["camper van", "Gas"]}
    with_room_topic(classification, ["schule"])      # tags: ['schule', 'lake']
    with_chosen_topics(classification, vocabulary)   # tags: ['schule', 'lake', 'van', 'utility']

Why a key of its own: the keywords name the specifics ('camping-gas'), and
asking the same list to hold the household's subjects pushed a small model
to pad every note with a generic topic. The topics are 0 to 3 picked from
a list, so a synonym may name one (`stack.filing.topic_list.chosen`), while a keyword
is written as it is (`Vocabulary.topics`).

The list comes from the vocabulary of the card's place
(`stack.filing.vocabulary`), so it never shows a person's private topic
where another person's card is filed. A note from a room of several
people is shown only the topics they may all read, because the reply
quotes what the model wrote; a private topic is offered in the person's
own room. The command `stack memory capture` has no room, so it is
offered only the shared topics: its receipt may be relayed into a room of
several people, and the model's title can echo a listed title. The list
is a JSON list in the prompt:
a title a family member wrote is data to pick from, never an instruction.

A topic room's id comes first and is written as the tag rule writes it,
never through a synonym: a room "Schule" keeps `schule`.

Pure: no I/O. The capture pipeline calls it; the prompt builder
(`pipeline._build_capture_prompt`) renders `tags_hint`, the keywords in
use and the topics, and asks for `TOPICS_FIELD`. Documents and email ask for no topics.
"""

from __future__ import annotations

import json

from stack.filing.topic_list import chosen

__all__ = ["TOPICS_FIELD", "TOPICS_RULE", "tags_hint", "topics_hint", "with_chosen_topics", "with_room_topic"]

# The key of the answer, as the prompt's JSON structure shows it. No
# minimum, unlike the keywords: an empty list is the answer for a note about
# nothing on the list (the diary's rule for its entries).
TOPICS_FIELD = (
    '"topics": ["0 to 3 topics from the household\'s topics above that this content is '
    'about, each as it is written there. An empty list when none fits; never pick one to '
    'fill the list."]')

TOPICS_RULE = (
    "- topics: 0 to 3, only from the household's topics. A specific keyword goes in tags, "
    "the subject in topics.")


def tags_hint(existing_tags: list[str], topics: list[str] | None) -> str:
    """The prompt's tags hint: the keywords already in use, then the
    household's `topics` when there are any (`topics_hint`).

        tags_hint(["lake"], ["Camper Van"])
        # Existing tags in use: ["lake"]
        # Prefer these when they fit. Only invent new tags when nothing existing matches.
        # The household's topics: ["Camper Van"]
    """
    keywords = (
        f"Existing tags in use: {json.dumps(existing_tags, ensure_ascii=False)}\n"
        "Prefer these when they fit. Only invent new tags when nothing existing matches.\n"
        if existing_tags
        else "No existing tags yet — invent useful single-word or two-word topic tags.\n")
    return keywords + (topics_hint(topics) if topics else "")


def topics_hint(topics: list[str]) -> str:
    """The prompt's line that lists the household's `topics` as one JSON list.

        topics_hint(['Camper "Van"', "Health"])
        # The household's topics: ["Camper \"Van\"", "Health"]
    """
    return f"The household's topics: {json.dumps(topics, ensure_ascii=False)}\n"


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


def with_chosen_topics(classification: dict, vocabulary) -> dict:
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


def _strings(value) -> list[str]:
    """A list of the non-empty strings in `value`, stripped; a lone string is a list of one."""
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return [v.strip() for v in values if isinstance(v, str) and v.strip()]


def _each_once(tags: list[str]) -> list[str]:
    return list(dict.fromkeys(tags))
