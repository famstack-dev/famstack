"""The prompt that works the family's corrections into a note.

A note refiled with corrections gets its clean version from a prompt of
its own (`note_correction`), before the capture prompt reads it. What the
model answers cannot be tested here, only what it is told: the note's
words and every correction with who wrote it, that a correction changes
the point it is about instead of being added to the note, and that a tag
a correction asks for is answered on its own, never in the text.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

from stacklets.docs.bot.note_correction import build_correction_prompt  # noqa: E402

WORDS = "garage sale saturday 9 to 1. prices: books 1 dollar, lamps 5"
CORRECTIONS = "Marge: it starts at 8, not 9\nHomer: lamps are 10"


def _prompt() -> str:
    return build_correction_prompt(words=WORDS, corrections=CORRECTIONS)


class TestTheModelIsToldToCorrectTheNoteInPlace:

    def test_it_reads_the_words_and_each_correction_with_who_wrote_it(self):
        prompt = _prompt()
        assert WORDS in prompt
        assert CORRECTIONS in prompt
        assert prompt.index(WORDS) < prompt.index(CORRECTIONS)

    def test_a_correction_replaces_what_it_corrects_and_is_never_appended(self):
        """Asked for the clean version along with everything else, the model
        kept the old point and appended the correction as one of its own."""
        prompt = " ".join(_prompt().lower().split())
        assert "replace" in prompt and "never appended" in prompt
        assert "right the first time" in prompt

    def test_the_note_keeps_its_language_and_who_corrected_it_stays_out(self):
        prompt = _prompt()
        assert "note's own language" in prompt
        assert "Who wrote a correction is not part of the note" in prompt

    def test_a_request_about_the_cards_fields_stays_out_of_the_text(self):
        """Given a reply like "Add tag birthday", when the note is corrected,
        then the prompt says such a request is no part of the note's text.
        Told only to add something new where it belongs, the model made the
        request a sentence of the note."""
        prompt = " ".join(_prompt().split())
        assert "request about the card's tags, title or people is not part of the note" in prompt

    def test_a_tag_a_correction_asks_for_is_answered_apart_from_the_text(self):
        """The classifier reads no correction, so the tag reaches the card
        only through this answer. A name or a value a correction gives is
        no request for a tag: "that was Bart, not Lisa" asks for none."""
        prompt = " ".join(_prompt().split())
        assert '"tags": ' in prompt
        assert "asks to add a tag" in prompt
        assert "A name or a value a correction gives is not a tag" in prompt

    def test_the_answer_holds_the_text_and_the_tags(self):
        assert '{"clean": ' in _prompt()
