"""Which people and tags a diary entry should get, and that a capture gets the same.

Two engines write people and tags on a card: the archivist's capture and the
diary's compile. The family reads both in one memory, so the same words must
give the same people and the same topics from either. A note may carry
keywords besides the topics; a diary card tags topics only.

The diary half drives `./stack memory diary` as the admin runs it (the
fixtures of `test_memory_diary_compile.py`: the stand-in Synapse, the bot
runner, the real curator over a bare repository) and reads the card the
curator then holds. The capture half is `tests/unit/stacklets/
test_capture_names_people_and_tags.py`. Both ask one model, at its
boundary (`tests.household`). The household is the Simpsons.

A test of behavior that is wrong today carries a marker of `tests.household`:
a strict expected failure whose reason states the wanted behavior. The mark
goes when the behavior is built.
"""

# ruff: noqa: F811  (a fixture imported from another test module is the test's parameter)
from __future__ import annotations

import pytest

from stack.frontmatter import parse
from tests.memory.unit.test_memory_diary_compile import (  # noqa: F401  (fixtures)
    ENTRIES, Diary, bot_command, bot_runner, brain, diary, room)
from tests.unit.stacklets.test_capture_names_people_and_tags import (  # noqa: F401  (fixtures)
    archivist, files, household, memory, mirror, vault)
from tests.household import HOUSEHOLD_FILES


# Names no one: the people on a card are the ones the model names.
WORDS = "We drove to the lake and stayed three days."


@pytest.fixture
def model(household) -> object:
    """The diary command's model client: the household's."""
    return household.llm()


def diary_card(memory, room, diary, words: str = WORDS) -> dict:
    """Marge writes `words` in the memories room and the admin compiles the
    diary: the one card it filed, with its path."""
    room.says("marge", words)
    diary.runs()
    (path, page), = memory.pages(ENTRIES).items()
    return {**parse(page), "path": path}


# ── A diary entry ────────────────────────────────────────────────────────

def test_a_diary_entry_names_the_known_people_and_the_ontologys_topics(memory, room, diary, household):
    """Given an entry naming Homer, Lisa and Mr. Burns, and the model's
    topics "camper van" and "health", then the card has the ids of the people
    the household declares and the id of the ontology's topic, and nothing
    for Mr. Burns."""
    household.says(persons=["Homer", "Lisa", "Mr. Burns"], tags=["camper van", "health"])

    card = diary_card(memory, room, diary)

    assert card["persons"] == ["homer", "lisa"]
    assert "health" in card["tags"]
    assert "Mr. Burns" not in memory.page(card["path"]).split("## Sources")[0]


def test_a_diary_entry_has_the_id_of_a_topic_card(memory, room, diary, household):
    """Given the model's topic "camper van", which is a topic card of the
    household, then the card's tags hold its id, with the ontology's `health`."""
    household.says(persons=["Homer"], tags=["camper van", "health"])

    assert diary_card(memory, room, diary)["tags"] == ["van", "health"]


def test_the_diary_model_is_shown_the_topics_with_their_names(memory, room, diary, household):
    """Given the household's topics (Health of the ontology, Camper Van and
    Quilt for Lisa as topic cards), then the prompt that reads an entry
    lists them by name, and the household's people; not Marge's own topic,
    because a diary card in the family's bucket is read by everyone."""
    household.says()

    diary_card(memory, room, diary)

    prompt = household.prompt_of("You are filing one entry")
    assert all(name in prompt for name in ("Homer", "Lisa", "Health", "Camper Van", "Quilt for Lisa"))
    assert "Homers 40th" not in prompt


# The household after `stack memory topic rename quilt-for-lisa lisas-quilt`:
# the topic card moved and lists its former id.
RENAMED = {**{k: v for k, v in HOUSEHOLD_FILES.items() if not k.startswith("family/quilt-for-lisa/")},
           "family/lisas-quilt/topic.md": "---\ntype: topic\ncard_id: topic-quilt1\ntitle: Lisa's Quilt\n"
                                          "former_topic_ids:\n- quilt-for-lisa\n---\n\n# Lisa's Quilt\n"}


@pytest.mark.parametrize("memory", [RENAMED], indirect=True, ids=["renamed"])
def test_a_diary_entry_naming_a_renamed_topic_gets_its_new_id(memory, room, diary, household):
    """Given the topic "Quilt for Lisa" was renamed "Lisa's Quilt" (id
    `lisas-quilt`), when the model still answers the old title, then the diary
    card has the new id."""
    household.says(persons=["Lisa"], tags=["Quilt for Lisa"])

    assert diary_card(memory, room, diary)["tags"] == ["lisas-quilt"]


# ── Both engines, side by side ───────────────────────────────────────────

# Each row: the words the model gives, as people and as tags (capture) or topics (diary).
SAME_WORDS = {
    "a person and a topic of the ontology": (["Homer"], ["Health"]),
    "a topic written in lower case": (["Homer"], ["health"]),
    "a topic by its German name": (["Homer"], ["Gesundheit"]),
    "two people": (["Homer", "Lisa"], ["Health"]),
    "a person by first name only": (["Abe"], ["Health"]),
    "a spelling from a person's page": (["Mr. Simpson"], ["Health"]),
    "a topic nobody declared": (["Homer"], ["Gardening"]),
    "a topic card of the household": (["Homer"], ["Camper Van"]),
    "a topic card, in lower case": (["Lisa"], ["quilt for lisa"]),
    "a name no declared person goes by": (["Homer", "Mr. Burns"], ["Health"]),
    "a person only the summary names": (["Homer"], ["Health"], "Lisa came along."),
    "a stranger only the summary names": (["Homer"], ["Health"], "Mr. Burns came along."),
}

def topics_of(card: dict, known: set[str]) -> list[str]:
    """The tags of a card that are topics (the ones a topic card or the ontology declares)."""
    return [t for t in card.get("tags") or [] if t in known]


@pytest.mark.parametrize("row", SAME_WORDS)
def test_a_capture_and_a_diary_entry_name_the_same_people_and_topics(row, memory, room, diary, household, archivist):
    """Given the same words from the model, as a note and as a diary entry,
    then the two cards have the same people and the same topics, and the
    note's About line keeps no name nobody goes by. A note may carry
    keywords besides the topics."""
    persons, tags, *summary = SAME_WORDS[row]
    household.says(persons=persons, tags=tags, summary=summary[0] if summary else None)
    topics = {"health", "van", "quilt-for-lisa"}

    note = files(archivist, memory, WORDS)
    entry = diary_card(memory, room, diary)

    assert note["persons"] == entry["persons"]
    assert "Mr. Burns" not in note["about"]
    assert topics_of(note, topics) == topics_of(entry, topics)
