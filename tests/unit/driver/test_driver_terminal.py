"""How the driver reads the installer's terminal and answers it.

The wizard asks with `› <prompt> [default]` and `? <question> [Y/n]`, drawn
with colour codes and spinners. The driver has to see a question where the
output stops at one, answer the ones it was given answers for, in order when
a prompt repeats, and know when it has no answer: that is where it stops,
instead of pressing Enter on a question nobody meant to confirm.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "driver"))

from chat import listing  # noqa: E402
from terminal import Answers, plain, prompt_in  # noqa: E402

COLOUR = "\x1b[38;5;208m"
RESET = "\x1b[0m"


class TestSeeingAQuestion:

    def test_a_prompt_with_a_default_is_a_question(self):
        assert prompt_in(f"\n  {COLOUR}›{RESET} Family name {COLOUR}[Simpson]{RESET} ") == "Family name"

    def test_a_confirmation_is_a_question(self):
        assert prompt_in("\n  ? Are you sure? [y/N] ") == "Are you sure?"

    def test_output_that_goes_on_is_not(self):
        assert prompt_in("  ✓  Starting containers\n") is None
        assert prompt_in("  ⠙  Building service") is None

    def test_the_transcript_is_text(self):
        assert plain(f"{COLOUR}ok{RESET}\r\nnext\rredraw") == "ok\nnext\nredraw"


class TestAnswering:

    def test_an_answer_matches_how_the_prompt_starts(self):
        answers = Answers(["Family name=Simpson"])
        assert answers.for_prompt("Family name") == "Simpson"

    def test_a_repeated_prompt_takes_its_answers_in_order(self):
        answers = Answers(["Name (leave empty=Marge", "Name (leave empty=Bart",
                           "Name (leave empty="])
        asked = [answers.for_prompt("Name (leave empty to continue)") for _ in range(4)]
        assert asked == ["Marge", "Bart", "", None]

    def test_a_question_without_an_answer_is_none(self):
        assert Answers(["Family name=Simpson"]).for_prompt("Are you sure?") is None


class TestShowingMessages:

    def test_a_listing_says_which_thread_or_message_an_answer_belongs_to(self):
        text = listing([
            {"sender": "archivist-bot", "event_id": "$b", "body": "Filed\nmore",
             "thread": "$a1234567890", "reply_to": None},
            {"sender": "marge", "event_id": "$c", "body": "this is Marge's car",
             "thread": None, "reply_to": "$b1234567890"},
        ])
        assert "[thread $a12345678]" in text and "more" not in text
        assert "[reply $b12345678]" in text


class TestTheProtocol:
    """Each step is a line on stderr, and in DRIVER_LOG when that names a file,
    so the driver calls of one scenario leave one protocol behind."""

    def test_steps_are_appended_to_the_protocol_file(self, tmp_path, monkeypatch):
        from narration import Narrator
        protocol = tmp_path / "run.log"
        monkeypatch.setenv("DRIVER_LOG", str(protocol))

        Narrator().when("marge says in picnic: 'hi'")
        Narrator().then("archivist answers in picnic")

        lines = protocol.read_text().splitlines()
        assert [line.split("] ", 1)[1].split()[0] for line in lines] == ["WHEN", "THEN"]
        assert "marge says in picnic" in lines[0]
