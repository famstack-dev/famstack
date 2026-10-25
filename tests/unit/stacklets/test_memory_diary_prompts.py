"""What the diary asks a model when it reads an entry for its card.

A diary card shows the family's words. What a model adds to them (a
title, a summary, facts) is only worth its place when it says something
the words do not: a short entry gets no summary, because its words are
shorter than any summary of them (memory cards review, C1).

These read the prompt as the model gets it and check what it asks for,
not how it phrases it.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

from stacklets.memory.bot import diary  # noqa: E402
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


def _flat(text: str) -> str:
    """`text` lowercased on one line, so a phrase is found across a line break."""
    return " ".join(text.split()).lower()


def _key(prompt: str, key: str) -> str:
    """What the prompt asks for under one key of the answer, lowercased."""
    start = prompt.index(f'\n"{key}":')
    end = prompt.find('\n"', start + 1)
    return _flat(prompt[start:end if end > 0 else None])


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


# ── Naming the people in an entry ─────────────────────────────────────

# Words and letters only German has. The prompts are written in English
# and the household's language is a variable they name, so none of these
# belongs in them.
_GERMAN = re.compile(r"[äöüßÄÖÜ]|\b(und|der|das|mit|einem|eine|bevor|während|erzählt)\b",
                     re.IGNORECASE)


def _every_prompt(monkeypatch) -> dict[str, str]:
    cli = _diary_command()
    return {"short entry": _prompt(SHORT, monkeypatch), "long entry": _prompt(LONG, monkeypatch),
            "month": cli._SUMMARY_PROMPT, "correction": cli._CORRECT_PROMPT}


class TestTheDiaryNamesItsPeople:
    """A card reads like the household's own when it names people. One
    that says "a child" reads like a stranger's family. When the words do
    not say who the other person was, the card names the people it could
    be, and a reply in the card's thread settles it."""

    def test_no_prompt_carries_german(self, monkeypatch):
        for name, prompt in _every_prompt(monkeypatch).items():
            assert not _GERMAN.search(prompt), f"{name}: {_GERMAN.search(prompt)[0]!r}"

    def test_no_example_puts_a_role_in_place_of_a_name(self, monkeypatch):
        for name, prompt in _every_prompt(monkeypatch).items():
            for role in ("one of the kids", "a child", "with a kid"):
                assert role not in _flat(prompt), f"{name} teaches {role!r}"

    def test_an_unnamed_person_is_named_as_the_people_it_could_be(self, monkeypatch):
        prompt = _flat(_prompt(LONG, monkeypatch))
        assert "the people it could be" in prompt
        assert '"or"' in prompt

    def test_the_rule_covers_a_short_entrys_title_too(self, monkeypatch):
        prompt = _flat(_prompt(SHORT, monkeypatch))
        assert "the people it could be" in prompt and "title" in prompt

    def test_the_candidates_are_the_cards_people(self, monkeypatch):
        assert "could be" in _key(_prompt(LONG, monkeypatch), "persons")

    def test_a_conversation_names_who_took_part_not_who_said_what(self, monkeypatch):
        summary = _key(_prompt(LONG, monkeypatch), "summary")
        assert "who took part" in summary and "never who said" in summary

    def test_the_month_names_people_or_leaves_them_out(self, monkeypatch):
        month = _flat(_every_prompt(monkeypatch)["month"])
        assert "could be" in month and "never put a role in place of a name" in month

    def test_a_correction_settles_who_it_was(self, monkeypatch):
        correction = _flat(_every_prompt(monkeypatch)["correction"])
        assert "could be" in correction and "drop the others" in correction


class TestThePeopleListOffersEveryCandidate:
    """The people an entry could be about are the people the family
    declared, a baby without a chat account included. The list the model
    reads names each of them by every name they go by, so it can pick the
    candidates out of it and a name it hears resolves to one of them."""

    def test_everyone_declared_is_on_the_list(self, tmp_path, monkeypatch):
        from test_memory_diary_people import HOMER, MAGGIE, TestThePersonFilesDecideWhoTheDiaryKnows
        bart = "---\ntype: person\ntitle: Bart Simpson\naccount: bart\n---\n"
        cli = TestThePersonFilesDecideWhoTheDiaryKnows._instance(
            tmp_path, monkeypatch, {"homer": HOMER, "bart": bart, "maggie": MAGGIE})
        _ontology, _people, section, _lang, _declared = cli._vocabulary("family")
        prompt = cli._extract_prompt(_entry(LONG), vocabulary="", people_section=section)

        for line in ("  - Homer (Homer Simpson)", "  - Bart (Bart Simpson)",
                     "  - Maggie (Maggie Simpson, Margaret)"):
            assert line in prompt
