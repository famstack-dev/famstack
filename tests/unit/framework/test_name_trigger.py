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

import pytest

from stack.name_trigger import addressed_by_name, addressed_by_voice, strip_address


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


class TestASpokenNameIsMatchedByHowItSounds:
    """Whisper writes a name the way it heard it: "Stacky" said in a German
    sentence came back as "Staki", and the agent stayed silent. In a
    transcript the name counts by its sound; typed text keeps the exact
    name, since a typed "Staki" is a different word."""

    @pytest.mark.parametrize("heard", [
        "Staki, setz Brot und Käse auf die Einkaufsliste",
        "Stacki, was steht auf der Liste?",
        "Stäcki, wir brauchen Milch",
        "Stachy, setz Brot und Käse auf die Einkaufsliste",
        "Stacey, put milk on the list",
        "Hey Staky, what's for dinner",
        "what's on the list, Staki?",
    ])
    def test_the_name_as_whisper_spells_it_still_addresses_the_agent(self, heard):
        assert addressed_by_voice(heard, "Stacky")

    @pytest.mark.parametrize("heard", [
        "Statis, setz Brot auf die Liste",
        "Sticky fingers again, Bart",
        "we told Staki about it yesterday",
    ])
    def test_a_word_that_sounds_otherwise_or_sits_mid_sentence_does_not(self, heard):
        assert not addressed_by_voice(heard, "Stacky")

    def test_typed_text_keeps_the_exact_name(self):
        assert not addressed_by_name("Staki, put milk on the list", "Stacky")


class TestAVowelWhisperHeardOtherwiseStillNamesTheAgent:
    """Whisper hears a name's vowels loosely. On the English test instance
    "Stacky" came back as "Stocky" at the start of a message and as
    "Stacke" at its end, and the agent stayed silent in a room with more
    people. One vowel among a, o and u, or a final e for a name that ends
    in i or y, still says the name. A family can rename its agent, so the
    rule is about how a name sounds, never about "Stacky"."""

    @pytest.mark.parametrize("name, heard", [
        ("Stacky", "Stocky, what's on the groceries list?"),
        ("Stacky", "Stucky, put milk on the list"),
        ("Stacky", "Answer in German, Stacke."),
        ("Stacky", "Stackie, what's for dinner"),
        ("Lotti", "Lotte, was steht auf der Einkaufsliste?"),
        ("Lotti", "Lotty, wir brauchen Milch"),
        ("Lotti", "Wann kommt der Bus, Lotte?"),
        ("Buddy", "Boddy, what's on the list?"),
        ("Buddy", "turn the porch light off, Budde"),
    ])
    def test_a_changed_vowel_still_addresses_the_agent(self, name, heard):
        assert addressed_by_voice(heard, name)

    @pytest.mark.parametrize("name, heard", [
        ("Stacky", "Sticky fingers again, Bart"),
        ("Stacky", "Statis, setz Brot auf die Liste"),
        ("Stacky", "we told Stocky about it yesterday"),
        ("Lotti", "Lotta, kommst du zum Essen?"),
        ("Lotti", "Latte macchiato für mich, bitte"),
        ("Lotti", "Wir brauchen Milch und Eier"),
        ("Buddy", "Biddy called about Saturday"),
    ])
    def test_any_other_word_or_place_is_no_address(self, name, heard):
        """Another vowel (i), another final letter ("Lotta" is someone
        else's name), two changes at once ("Latte"), the name in the
        middle of a sentence, or nothing like it at all: each of these is
        a family member talking, and the agent keeps out of it."""
        assert not addressed_by_voice(heard, name)

    def test_a_short_name_keeps_its_vowels(self):
        """In three letters a changed vowel is a different word: an agent
        called "Rob" must not answer "Rub the pan with butter first"."""
        assert addressed_by_voice("Robb, what's on the list?", "Rob")
        assert not addressed_by_voice("Rub the pan with butter first", "Rob")
        assert not addressed_by_voice("Mux, what's on the list?", "Max")

    def test_a_name_of_several_words_is_said_exactly(self):
        assert addressed_by_voice("family bot, what's on the list?", "Family Bot")
        assert not addressed_by_voice("fomily bot, what's on the list?", "Family Bot")

    def test_a_renamed_agent_no_longer_answers_to_the_old_name(self):
        assert not addressed_by_voice("Stocky, what's on the list?", "Lotti")
        assert not addressed_by_voice("Stacky, what's on the list?", "Lotti")

    def test_typed_text_still_needs_the_exact_name(self):
        assert not addressed_by_name("Stocky, what's on the groceries list?", "Stacky")
        assert not addressed_by_name("Lotte, wir brauchen Milch", "Lotti")


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


class TestWhatIsLeftForTheBot:
    """After the address, the bot hears the question, not its own name."""

    @pytest.mark.parametrize("body, question", [
        ("Archivist, when does the car insurance renew?", "when does the car insurance renew?"),
        ("archivist when does the car insurance renew?", "when does the car insurance renew?"),
        ("hey Archivist: what did Lenny suggest?", "what did Lenny suggest?"),
        ("what did Lenny suggest, Archivist?", "what did Lenny suggest?"),
    ])
    def test_the_address_is_dropped(self, body, question):
        assert strip_address(body, "Archivist") == question

    def test_a_message_about_the_bot_is_left_as_it_is(self):
        body = "I asked the Archivist and it said no"
        assert strip_address(body, "Archivist") == body
