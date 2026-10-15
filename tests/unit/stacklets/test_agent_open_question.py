"""When does a message answer the agent's own question?

The agent asks before acting on anything unclear, and in a room with
several people the answer rarely repeats the agent's name ("Ja, die
können weg."). These pin who may answer without the name, and for how
long: the person asked, once, while the question is fresh.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "stacklets" / "agent" / "runtime"))

from open_question import WINDOW_SECONDS, OpenQuestions, asks  # noqa: E402

KITCHEN, HOMER, MARGE = "!kitchen", "@homer:simpson", "@marge:simpson"


def _asked_homer() -> OpenQuestions:
    questions = OpenQuestions()
    questions.asked(KITCHEN, HOMER, at=100.0)
    return questions


def test_the_person_asked_answers_without_the_name():
    assert _asked_homer().answers(KITCHEN, HOMER, at=130.0)


def test_only_their_first_message_counts():
    questions = _asked_homer()
    questions.answers(KITCHEN, HOMER, at=130.0)
    assert not questions.answers(KITCHEN, HOMER, at=140.0)


def test_someone_else_still_needs_the_name_and_leaves_the_question_open():
    questions = _asked_homer()
    assert not questions.answers(KITCHEN, MARGE, at=110.0)
    assert questions.answers(KITCHEN, HOMER, at=120.0)


def test_an_answer_long_after_the_question_is_not_one():
    assert not _asked_homer().answers(KITCHEN, HOMER, at=100.0 + WINDOW_SECONDS + 1)


def test_another_room_is_another_conversation():
    assert not _asked_homer().answers("!garage", HOMER, at=110.0)


def test_whatever_the_agent_says_next_closes_the_question():
    questions = _asked_homer()
    questions.closed(KITCHEN)
    assert not questions.answers(KITCHEN, HOMER, at=110.0)


def test_a_question_is_one_that_ends_asking():
    assert asks("Die Partyliste hat noch 2 offene Punkte. Sollen die auch weg?")
    assert asks("Which one, the oat milk or the whole milk? 🥛")
    assert not asks("Is it the oat milk? I put it on the list.")
    assert not asks("Milch steht jetzt auf der Einkaufsliste.")
