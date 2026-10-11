"""The home page: the template holds the layout, the model reads the sources.

Read from the outside, as the family reads the published page. What
these tests promise:

- Everyone the person files declare is on the page, linked to their
  page, once, in the order the model gives (oldest first); a person the
  model forgot is still there, a person it made up is not.
- A section with nothing in it is not on the page, heading included.
- A German household gets German headings.
- An answer the compiler cannot read leaves the page as it was.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot" / "cli"))

import wiki  # noqa: E402
from stack.people import Person  # noqa: E402

PEOPLE = {
    "bart": Person("bart", "Bart Simpson", account="bart"),
    "homer": Person("homer", "Homer Simpson", account="homer"),
    "maggie": Person("maggie", "Maggie Simpson"),
}
ROSTER = ["bart", "homer", "maggie"]
INDEX = [{"rel": "family/documents/2009/06/deed-p1.md", "title": "Deed", "date": "2009-06-15",
          "summary": "742 Evergreen Terrace, bought by Homer and Marge."}]

ANSWER = {
    "family": "The Simpsons",
    "address": "742 Evergreen Terrace",
    "welcome": "A loud, loving family on Evergreen Terrace. [1]",
    "order": ["homer", "bart", "maggie"],
    "people": {"homer": {"line": "Dad, safety inspector", "cite": [1]},
               "bart": {"line": "The prankster"},
               "flanders": {"line": "Not ours"}},
    "relatives": [{"name": "Abraham Simpson", "detail": "Homer's father", "cite": [1]}],
    "friends": [{"name": "Ned Flanders", "detail": "the neighbour", "cite": [1]}],
    "home": "Owned since 2009. [1]",
    "vehicles": [], "insurance": [],
}


class _Model:
    def __init__(self, answer) -> None:
        self.answer = answer

    async def complete(self, role, prompt, **kwargs) -> str:
        return self.answer if isinstance(self.answer, str) else json.dumps(self.answer)


@pytest.fixture
def brain(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_REPO_DIR", str(tmp_path))
    (tmp_path / "index.md").write_text(
        "---\ntitle: Family Memory\ngenerated: true\n---\n", encoding="utf-8")
    return tmp_path


def _home(brain: Path, answer, lang: str = "en") -> str:
    rc = asyncio.run(wiki._generate_home(
        _Model(answer), INDEX, roster=ROSTER, people=PEOPLE,
        shared_bucket="family", lang=lang, write=True))
    assert rc == 0
    return (brain / "index.md").read_text(encoding="utf-8")


def _family(page: str) -> list[str]:
    return re.findall(r"^- \*\*\[[^\]]+\]\((\w+)/about\)\*\*", page, re.M)


class TestEveryoneIsOnThePage:
    def test_in_the_models_order_oldest_first(self, brain):
        assert _family(_home(brain, ANSWER)) == ["homer", "bart", "maggie"]

    def test_a_person_the_model_left_out_is_still_listed(self, brain):
        """Maggie has no line from the model and no place in its order."""
        page = _home(brain, ANSWER)
        assert "- **[Maggie Simpson](maggie/about)**\n" in page + "\n"

    def test_a_person_the_model_made_up_is_not(self, brain):
        assert "Not ours" not in _home(brain, ANSWER)

    def test_the_declared_name_wins_over_the_models(self, brain):
        answer = {**ANSWER, "people": {"homer": {"name": "Homer J.", "line": "Dad"}}}
        assert "- **[Homer Simpson](homer/about)** · Dad." in _home(brain, answer)


class TestTheSectionsAroundThem:
    def test_relatives_and_friends_are_listed_apart(self, brain):
        page = _home(brain, ANSWER)
        assert "## Relatives\n- **Abraham Simpson** · Homer's father. [1]" in page
        assert "## Friends and neighbours\n- **Ned Flanders** · the neighbour. [1]" in page

    def test_an_empty_section_is_not_on_the_page(self, brain):
        page = _home(brain, ANSWER)
        assert "## Vehicles" not in page and "## Insurance" not in page

    def test_a_german_household_reads_german_headings(self, brain):
        page = _home(brain, ANSWER, lang="de")
        assert "## Familie" in page and "## Verwandte" in page
        assert "## Freunde und Nachbarn" in page and "## Zuhause" in page


class TestAnAnswerThatCannotBeRead:
    def test_the_page_stays_as_it_was(self, brain):
        before = _home(brain, ANSWER)
        rc = asyncio.run(wiki._generate_home(
            _Model("# The Simpsons\n..."), INDEX, roster=ROSTER, people=PEOPLE,
            shared_bucket="family", lang="en", write=True))
        assert rc == 1
        assert (brain / "index.md").read_text(encoding="utf-8") == before


class TestTheModelIsToldWhoIsWho:
    def test_every_name_a_person_goes_by(self):
        """A birth certificate says "Margaret"; she is Maggie, not a twin."""
        prompt = wiki.home_page.prompt(
            evidence="", ids=["maggie"], names={"maggie": "Maggie Simpson"}, lang="en",
            aliases={"maggie": ["Margaret", "Mags"]})
        assert "- maggie: Maggie Simpson (also called Margaret, Mags)" in prompt


class TestOneStyleWhateverTheModelWrites:
    def test_a_citation_inside_a_line_is_shown_once(self, brain):
        answer = {**ANSWER, "relatives": [{"name": "Abraham Simpson",
                                           "detail": "Homer's father [1].", "cite": [1]}]}
        assert "- **Abraham Simpson** · Homer's father. [1]\n" in _home(brain, answer)

    def test_a_person_links_to_their_page_instead_of_citing(self, brain):
        """Their own page cites every fact; one line here is not evidence."""
        answer = {**ANSWER, "people": {"homer": {"line": "Our dad [8].", "cite": [8]}}}
        assert "- **[Homer Simpson](homer/about)** · Our dad.\n" in _home(brain, answer)

    def test_the_people_who_help_the_family_have_their_own_section(self, brain):
        answer = {**ANSWER, "helpers": [{"name": "Dr. Hibbert", "detail": "family doctor", "cite": [1]}]}
        page = _home(brain, answer)
        assert "## The people who help us\n- **Dr. Hibbert** · family doctor. [1]" in page
