"""When does a family-room message count as talking to the agent?

The rule these tests pin is a product decision, not an implementation
detail: people in a family room type "Stacky, what's on our list?"
rather than autocompleting a Matrix pill, and an agent that answers
only pills looks broken to everyone who does not know what a pill is.

Read this file as the spec for that rule. The cases are the sentences
a family actually sends, and the boundary they draw is between speaking
*to* the agent and speaking *about* it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "agent" / "runtime"))

from name_trigger import addressed_by_name  # noqa: E402


class TestSpeakingToTheAgent:

    @pytest.mark.parametrize("body", [
        "Stacky, what's on our list?",
        "Stacky what's on our list?",
        "Stacky: what's on our list?",
        "hey Stacky, can you strike item 3",
        "Hey Stacky can you strike item 3",
        "ok Stacky, add milk to the shopping list",
        "what's on our list, Stacky?",
        "can you strike the camera one, Stacky",
    ])
    def test_the_agent_is_being_addressed(self, body):
        assert addressed_by_name(body, "Stacky")

    def test_case_never_matters(self):
        """Nobody capitalises consistently on a phone keyboard."""
        for body in ("stacky, whats on our list", "STACKY WHATS ON OUR LIST",
                     "StAcKy whats on our list"):
            assert addressed_by_name(body, "Stacky"), body

    def test_a_reply_still_reads_as_an_address(self):
        """Matrix puts the quoted message in the body before the reply.

        Following up on something with "Stacky, ..." is ordinary in a
        busy room, and the sender's own words are never at position zero
        when they do.
        """
        body = (
            "> <@marge:simpson> shall we sort the camping trip?\n"
            "\n"
            "Stacky, what's on our list?"
        )
        assert addressed_by_name(body, "Stacky")


class TestSpeakingAboutTheAgent:
    """The agent must not butt into a conversation about itself."""

    @pytest.mark.parametrize("body", [
        "I asked Stacky and it said no",
        "we should get Stacky to do this",
        "does anyone else find Stacky slow?",
        "the Stacky thing worked well yesterday",
    ])
    def test_a_mid_sentence_name_is_not_an_address(self, body):
        assert not addressed_by_name(body, "Stacky")

    def test_an_unrelated_message_is_left_alone(self):
        assert not addressed_by_name("what's on our list?", "Stacky")

    def test_a_longer_word_starting_with_the_name_is_not_the_name(self):
        assert not addressed_by_name("Stackyish behaviour again", "Stacky")


class TestTheNameIsConfigured:
    """`AGENT_NAME` is a family's choice, so nothing may assume "Stacky"."""

    def test_a_renamed_agent_answers_to_its_own_name(self):
        assert addressed_by_name("Kit, whats on the list", "Kit")
        assert addressed_by_name("kit, whats on the list", "Kit")

    def test_a_renamed_agent_stops_answering_to_the_old_one(self):
        assert not addressed_by_name("Stacky, whats on the list", "Kit")

    def test_a_name_with_a_space_still_works(self):
        assert addressed_by_name("family bot, whats on the list", "Family Bot")

    def test_a_name_with_regex_characters_is_matched_literally(self):
        """A name is text a family typed, never a pattern.

        Unescaped, the `.` in "Mr. Bot" matches any character, so the
        agent would answer to "Mr! Bot" and anything else shaped like
        it.
        """
        assert addressed_by_name("mr. bot, whats on the list", "Mr. Bot")
        assert not addressed_by_name("mr! bot, whats on the list", "Mr. Bot")

    def test_a_name_ending_in_punctuation_still_matches(self):
        """A word boundary needs a word character to sit against, which
        a name like this never provides."""
        assert addressed_by_name("c++, whats on the list", "C++")


class TestAnUnconfiguredNameMatchesNothing:

    @pytest.mark.parametrize("name", ["", "   ", None])
    def test_a_blank_name_never_triggers(self, name):
        """The dangerous default.

        An empty name compiles to a pattern that matches everywhere, so
        getting this wrong turns every message in every room into an
        address and the agent answers all of them.
        """
        assert not addressed_by_name("Stacky, what's on our list?", name)

    def test_an_empty_message_is_not_an_address(self):
        assert not addressed_by_name("", "Stacky")
        assert not addressed_by_name("> <@marge:simpson> quoted only\n", "Stacky")
