"""Only a note in the writer's own words becomes todos.

Copied text carries the suggestions of whoever wrote it, and a model
asked for tasks finds them: a pasted article put "try this framework"
on a family's list. The gate keeps that text away from the question.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "stacklets" / "docs" / "bot"))

from own_words import MAX_TYPED_CHARS, in_own_words  # noqa: E402


class TestWhatCountsAsTheWritersOwnWords:
    def test_a_short_typed_reminder(self):
        assert in_own_words("wir sollten bis Freitag den Müll rausbringen", spoken=False)

    def test_a_voice_message_of_any_length(self):
        assert in_own_words("Also, ich hab mir überlegt … " * 40, spoken=True)


class TestWhatCountsAsCopiedText:
    def test_a_paste_longer_than_a_chat_message(self):
        assert not in_own_words("x" * (MAX_TYPED_CHARS + 1), spoken=False)

    def test_text_with_a_link(self):
        assert not in_own_words("Try Bun, it is fast: https://bun.sh", spoken=False)

    def test_several_paragraphs(self):
        assert not in_own_words("Why we moved to Bun\n\nWe tried it for a week.", spoken=False)

    def test_nothing_at_all(self):
        assert not in_own_words("   ", spoken=False)
