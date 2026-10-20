"""What the diary asks a model when it reads an entry for its card.

A diary card shows the family's words. What a model adds to them (a
title, a summary, facts) is only worth its place when it says something
the words do not: a short entry gets no summary, because its words are
shorter than any summary of them (memory cards review, C1).

These read the prompt as the model gets it and check what it asks for,
not how it phrases it.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot"))

import diary  # noqa: E402
from test_memory_diary_people import _diary_command  # noqa: E402

PEOPLE = "Family members (canonical first name; synonyms in parens):\n  - Marge\n  - Bart"
SHORT = "Bart, did you put the cat in the dryer again? No way, Dad."
LONG = ("So, tell me again what happened at school today? We had a fire drill and "
        "the whole class had to stand outside in the rain for twenty minutes. ") * 4


def _entry(body: str) -> diary.Entry:
    return diary.Entry(on=date(2026, 10, 18), confidence="sent", basis="",
                       kind="voice", sender="marge", body=body, event_ids=["$e"])


def _prompt(body: str, monkeypatch) -> str:
    monkeypatch.setenv("LANGUAGE", "en")
    return _diary_command()._extract_prompt(_entry(body), vocabulary="", people_section=PEOPLE)


def _key(prompt: str, key: str) -> str:
    """What the prompt asks for under one key of the answer, lowercased."""
    start = prompt.index(f'\n"{key}":')
    end = prompt.find('\n"', start + 1)
    return prompt[start:end if end > 0 else None].lower()


class TestAShortEntryIsAskedForNoSummary:

    def test_a_short_entry_is_asked_for_an_empty_summary(self, monkeypatch):
        assert "empty string" in _key(_prompt(SHORT, monkeypatch), "summary")

    def test_its_facts_never_say_the_entry_again(self, monkeypatch):
        facts = _key(_prompt(SHORT, monkeypatch), "facts")
        assert "never a line that says part of the entry again" in facts

    def test_a_long_entry_is_asked_for_a_few_sentences(self, monkeypatch):
        summary = _key(_prompt(LONG, monkeypatch), "summary")
        assert "empty string" not in summary
        assert "sentences" in summary

    def test_the_entry_itself_is_in_the_prompt(self, monkeypatch):
        assert SHORT in _prompt(SHORT, monkeypatch)
