"""The check a note's clean version passes before it is filed.

A model asked to tidy a note sometimes drops or changes what the note is
for: the amount, the day, the name. A tidy text that lost "200" reads as
if it were complete, so the clean version is checked against the words
in code, and one that fails is not filed (`clean_words.kept`).
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from clean_words import CLEAN_MAX_CHARS, is_cleanable, kept, kept_rebuilt  # noqa: E402

MESSY = ("ok so for the trip: homer needs to call the hotel abt the late checkin, "
         "bart forgot his swimsuit AGAIN buy one at the mall b4 sat, also lisa wants "
         "the museum thing on sunday not sat. budget max 200")
CLEAN = ("For the trip:\n\n"
         "- Homer needs to call the hotel about the late check-in.\n"
         "- Bart forgot his swimsuit again; buy one at the mall before Saturday.\n"
         "- Lisa wants the museum visit on Sunday, not Saturday.\n"
         "- Budget: at most 200.")


class TestAFaithfulCleanVersionIsFiled:

    def test_shorthand_written_out_and_a_list_made_of_it_passes(self):
        assert kept(MESSY, CLEAN) == (CLEAN, "")

    def test_a_digit_inside_shorthand_may_be_written_out(self):
        assert kept("see you b4 the game", "See you before the game.")[0]

    def test_the_word_that_opens_a_sentence_is_not_taken_for_a_name(self):
        words = "Also we need bread. Then call Patty"
        assert kept(words, "We need bread. Call Patty.")[0]

    def test_a_name_whose_case_changed_is_still_kept(self):
        assert kept("tell Moe AGAIN abt the tab", "Tell Moe again about the tab.")[0]


class TestACleanVersionThatChangedTheNoteIsNot:

    def test_one_that_lost_a_number(self):
        clean, why = kept(MESSY, CLEAN.replace("at most 200", "keep it cheap"))
        assert clean == ""
        assert "200" in why

    def test_one_that_lost_a_name(self):
        words = "pick up the cake at Lard Lad on friday"
        clean, why = kept(words, "Pick up the cake on Friday.")
        assert clean == ""
        assert "Lard" in why

    def test_one_that_added_a_number(self):
        clean, why = kept("dentist next friday", "Dentist on 2026-10-30.")
        assert clean == ""
        assert "2026" in why

    def test_one_that_carries_a_heading(self):
        clean, why = kept("milk eggs", "## Shopping\n\n- Milk\n- Eggs")
        assert clean == "" and why


class TestACorrectionMayChangeWhatTheCleanVersionSays:

    def test_a_number_the_correction_names_may_replace_the_notes(self):
        words = "budget max 200"
        clean, why = kept(words, "Budget: at most 250.", corrections="Marge: 250, not 200")
        assert (clean, why) == ("Budget: at most 250.", "")

    def test_without_the_correction_the_same_change_fails(self):
        assert kept("budget max 200", "Budget: at most 250.")[0] == ""

    def test_a_correction_that_only_gives_the_new_number_replaces_the_old_one(self):
        clean, why = kept("budget max 200", "Budget: at most 250.",
                          corrections="Marge: budget is 250 now")
        assert (clean, why) == ("Budget: at most 250.", "")

    def test_one_new_number_stands_in_for_one_lost_number_only(self):
        clean, why = kept("budget max 200, 3 nights", "Budget: at most 250.",
                          corrections="Marge: budget is 250 now")
        assert clean == "" and "lost 3" in why

    def test_a_correction_may_replace_a_name_with_another(self):
        words = "the museum trip at Springfield Museum"
        clean, why = kept(words, "The museum trip at the Science Museum of Shelbyville.",
                          corrections="Lisa: we go to the Science Museum of Shelbyville instead")
        assert why == "" and "Shelbyville" in clean


class TestANoteThatNeedsNoCleaningIsShownOnce:

    def test_the_same_words_with_a_full_stop_are_no_clean_version(self):
        words = "Bart's dentist moved to Thursday"
        assert kept(words, "Bart's dentist moved to Thursday.") == ("", "")

    def test_no_answer_is_no_clean_version(self):
        assert kept(MESSY, "") == ("", "")


class TestWhichNotesAreCleaned:

    def test_a_note_typed_into_a_chat(self):
        assert is_cleanable(MESSY)

    def test_not_a_text_too_long_to_have_been_typed_as_a_note(self):
        assert not is_cleanable("word " * (CLEAN_MAX_CHARS // 4))

    def test_not_an_empty_one(self):
        assert not is_cleanable("  \n")


class TestARebuiltTextKeepsTheNoteLessWhatWasReplaced:
    """A note with corrections or a person's edits is rebuilt from its
    events (`kept_rebuilt`, ADR-018 §7). It is checked by the first
    filing's rule, every number and name of the words is kept, less what
    an event replaced; beyond that it is not refused for its wording. The
    events are given as what they say, without who said it
    (`memory.card_events.text_of`)."""

    def test_a_new_value_in_place_of_the_old_passes(self):
        assert kept_rebuilt("budget max 200", "Budget: at most 250.",
                            events="budget is 250 now") == ("Budget: at most 250.", "")

    def test_of_two_corrections_of_one_value_the_later_one_passes(self):
        """The earlier correction's 250 is not asked for: the later one replaced it."""
        assert kept_rebuilt("budget max 200", "Budget: at most 300.",
                            events="budget is 250 now\nno, 300") == ("Budget: at most 300.", "")

    def test_one_that_kept_the_old_value_is_not_refused_for_it(self):
        """Whether it took the correction is the benchmark's to watch, not the check's."""
        assert kept_rebuilt("budget max 200", "Budget: at most 200.",
                            events="budget is 250 now")[1] == ""

    def test_one_that_lost_what_nobody_corrected_is_refused(self):
        clean, why = kept_rebuilt("12 kids, cake from Lard Lad", "15 kids.",
                                  events="15 kids now, not 12")
        assert clean == "" and "Lard" in why

    def test_a_value_an_edit_replaced_is_not_asked_for(self):
        assert kept_rebuilt("bring 3 sleeping bags, leave at 4", "Bring 4 sleeping bags. Leave at 4.",
                            events="Bring 3 sleeping bags.\nBring 4 sleeping bags.")[1] == ""

    def test_a_number_nobody_gave_is_refused(self):
        clean, why = kept_rebuilt("budget max 200", "Budget: at most 250, plus 40 for gas.",
                                  events="budget is 250 now")
        assert clean == "" and "added 40" in why

    def test_an_empty_answer_is_refused(self):
        clean, why = kept_rebuilt("budget max 200", "", events="250")
        assert clean == "" and why
