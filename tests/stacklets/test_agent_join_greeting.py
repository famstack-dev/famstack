"""What the agent is asked to say when it joins a room.

A greeting is the first thing a family ever sees the agent do, so the
cases here are about first impressions rather than mechanics: the room
with a topic, and the room without one. The second is the one that bit
us. Asked to describe a topic that does not exist, the model answered
"let me check what this room is about" and then never spoke again, which
reads as a broken bot rather than a quiet one.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "agent" / "runtime"))

from brief import topic_for_room_label  # noqa: E402
from join_greeting import greeting_prompt  # noqa: E402


class TestARoomWithATopic:

    def test_the_greeting_names_that_topic(self):
        prompt = greeting_prompt("camping")
        assert "camping" in prompt

    def test_it_is_asked_for_the_todo_state(self):
        """The count and an item or two are the useful part of a topic
        greeting; without them it is just an introduction."""
        prompt = greeting_prompt("camping").lower()
        assert "todo" in prompt


class TestARoomWithoutATopic:
    """A DM, or any room whose name maps to no topic page."""

    def test_it_is_told_not_to_describe_a_topic(self):
        prompt = greeting_prompt("").lower()
        assert "no topic page" in prompt

    def test_it_is_told_what_to_say_instead(self):
        prompt = greeting_prompt("").lower()
        assert "introduce yourself" in prompt

    def test_no_topic_slug_leaks_into_the_prompt(self):
        """Guards the branch: the topic wording must be gone entirely,
        not merely have an empty slug interpolated into it."""
        assert "this room's topic" not in greeting_prompt("").lower()


class TestBothGreetingsShareTheHouseStyle:

    def test_the_model_is_told_to_look_first_and_write_once(self):
        """The double-post fix.

        The model narrated "let me check how many there are" as its own
        message, then answered in a second one, so the family saw the
        agent introduce itself twice.
        """
        for prompt in (greeting_prompt("camping"), greeting_prompt("")):
            low = prompt.lower()
            assert "before you write" in low
            assert "exactly one message" in low

    def test_no_customer_service_sign_off(self):
        """"Let me know if you'd like help with anything else!" is the
        register the project's voice rules exist to prevent."""
        for prompt in (greeting_prompt("camping"), greeting_prompt("")):
            assert "sign-off" in prompt.lower()


class TestResolvingARoomToItsTopic:
    """The lookup that decides which of the two greetings is used."""

    def test_a_topic_room_resolves_when_its_page_exists(self, tmp_path):
        page = tmp_path / "family" / "camping" / "about.md"
        page.parent.mkdir(parents=True)
        page.write_text("# Camping", encoding="utf-8")

        assert topic_for_room_label("Topic: Camping", tmp_path) == "camping"

    def test_a_topic_room_with_no_page_yet_resolves_to_nothing(self, tmp_path):
        """Pages are generated, so a brand-new topic room has none.

        It must fall back to the plain greeting rather than promise a
        description of a page that has not been written.
        """
        assert topic_for_room_label("Topic: Camping", tmp_path) == ""

    def test_a_dm_resolves_to_nothing(self, tmp_path):
        assert topic_for_room_label("Bart ⇄ Stacky", tmp_path) == ""
