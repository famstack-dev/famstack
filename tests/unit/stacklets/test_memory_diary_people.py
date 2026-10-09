"""Who the diary knows: the household as transcription and the cards see it.

Whisper decodes a voice note against the family's names, and the diary
cards list the people a note is about from the same list. A name missing
here is misheard ("Maggie" as "Eggie") and left off the card. So the list
starts from the person files (`family/people/`), or from everyone in
users.toml (FAMILY_NAMES) until they exist, and the wiki's person pages
add the spellings documents used.
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


# ── With person files ──────────────────────────────────────────────

def _diary_command():
    """Load `bot/cli/diary.py` as part of its package, under a name of its own.

    Several stacklets ship a `cli` package; importing this one as `cli`
    would shadow theirs for every later test.
    """
    import importlib
    import importlib.util
    # In the bot-runner, `voice` and the framework sit at /app.
    sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))
    sys.path.insert(0, str(_REPO_ROOT / "stacklets"))
    name = "memory_bot_cli"
    if name not in sys.modules:
        pkg_dir = _REPO_ROOT / "stacklets" / "memory" / "bot" / "cli"
        spec = importlib.util.spec_from_file_location(
            name, pkg_dir / "__init__.py", submodule_search_locations=[str(pkg_dir)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        spec.loader.exec_module(package)
    return importlib.import_module(f"{name}.diary")


HOMER = "---\ntype: person\ntitle: Homer Simpson\naccount: homer\n---\n"
MAGGIE = "---\ntype: person\ntitle: Maggie Simpson\naliases:\n  - Margaret\n---\n"


class TestThePersonFilesDecideWhoTheDiaryKnows:
    """Once person files exist, the diary listens for everyone they declare.

    The accounts (FAMILY_NAMES) only stand in until then. Maggie has no
    account; her file makes her known, and her aliases are names whisper
    should expect and a card can use.
    """

    @staticmethod
    def _instance(tmp_path, monkeypatch, people: dict[str, str] | None):
        vault, brain = tmp_path / "vault", tmp_path / "brain"
        (vault / "family").mkdir(parents=True)
        (brain / "media").mkdir(parents=True)
        (brain / "media" / "about.md").write_text(
            "---\ntitle: Margaret\nslug: media\ntype: person\ncanonical: Margaret\n---\n")
        for person_id, text in (people or {}).items():
            (vault / "family" / "people").mkdir(parents=True, exist_ok=True)
            (vault / "family" / "people" / f"{person_id}.md").write_text(text)
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(vault))
        monkeypatch.setenv("BRAIN_REPO_DIR", str(brain))
        monkeypatch.setenv("SHARED_BUCKET", "family")
        monkeypatch.setenv("FAMILY_NAMES", "Homer Simpson,Marge Simpson")
        return _diary_command()

    def test_before_the_files_the_accounts_and_pages_as_before(self, tmp_path, monkeypatch):
        cli = self._instance(tmp_path, monkeypatch, None)
        assert cli._household_people() == ["Homer Simpson", "Marge Simpson", "Margaret"]

    def test_with_files_every_person_and_every_name_they_go_by(self, tmp_path, monkeypatch):
        cli = self._instance(tmp_path, monkeypatch, {"homer": HOMER, "maggie": MAGGIE})
        assert cli._household_people() == [
            "Homer Simpson", "Homer", "Maggie Simpson", "Maggie", "Margaret"]

    def test_a_page_left_over_for_a_folder_is_not_family(self, tmp_path, monkeypatch):
        """The old roster made `media/` a person page; it no longer counts."""
        cli = self._instance(tmp_path, monkeypatch, {"homer": HOMER})
        assert cli._household_people() == ["Homer Simpson", "Homer"]

    def test_a_card_about_margaret_is_about_maggie(self, tmp_path, monkeypatch):
        cli = self._instance(tmp_path, monkeypatch, {"homer": HOMER, "maggie": MAGGIE})
        _ontology, people, section, _lang = cli._vocabulary("family")
        assert people["margaret"] == "Maggie"
        assert people["maggie simpson"] == "Maggie"
        assert "  - Maggie (Maggie Simpson, Margaret)" in section
