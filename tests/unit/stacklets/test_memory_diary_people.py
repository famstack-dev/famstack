"""Who the diary knows: the household as transcription and the cards see it.

Whisper decodes a voice note against the family's names, and the diary
cards list the people a note is about from the same list. A name missing
here is misheard ("Maggie" as "Eggie") and left off the card. So the list
starts from everyone in users.toml, rendered into FAMILY_NAMES, and the
wiki's person pages add the family's own spellings and nicknames.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot"))

import diary  # noqa: E402


def _page(name: str, synonyms=()) -> dict:
    return {"type": "person", "canonical": name, "synonyms": list(synonyms)}


class TestTheHouseholdTheDiaryKnows:

    def test_everyone_in_users_toml_before_any_filing_names_them(self):
        assert diary.household("Homer,Marge,Bart,Lisa", []) == ["Homer", "Marge", "Bart", "Lisa"]

    def test_person_pages_add_nicknames_without_repeating_a_name(self):
        assert diary.household("Homer,Marge", [_page("Marge", ["Margie"])]) == ["Homer", "Marge", "Margie"]

    def test_a_person_page_for_someone_without_an_account_counts(self):
        assert diary.household("Homer", [_page("Maggie")]) == ["Homer", "Maggie"]

    def test_pages_that_are_not_people_add_nothing(self):
        assert diary.household("Homer", [{"type": "topic", "canonical": "Camping"}]) == ["Homer"]

    def test_no_names_and_no_pages_is_an_empty_household(self):
        assert diary.household("", []) == []


class TestThePeopleACardCanBeAbout:
    """A card names the people a note is about, matched by any name they go by."""

    @staticmethod
    def _person(canonical, *names):
        from types import SimpleNamespace
        return SimpleNamespace(canonical=canonical, all_known_names=lambda: [canonical, *names])

    def test_a_member_without_a_page_is_still_someone_a_card_can_name(self):
        people = diary.card_people([self._person("Marge", "Margie")], ["Homer", "Marge", "Bart"])

        assert people == {"marge": "Marge", "margie": "Marge", "homer": "Homer", "bart": "Bart"}

    def test_before_any_page_the_household_alone(self):
        assert diary.card_people([], ["Homer", "Bart"]) == {"homer": "Homer", "bart": "Bart"}
