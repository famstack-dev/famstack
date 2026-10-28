"""The home page: the template holds the layout, the model reads the sources.

Read from the outside, as the family reads the published page. What
these tests promise:

- Everyone the person files declare is on the page, linked to their
  page, once, in the order the model gives (oldest first); a person the
  model forgot is still there, a person it made up is not.
- The household facts a newcomer wants first sit at the top, and the
  pets are listed with the family.
- What the family did in the last month is told as a short paragraph,
  from that month's filings only; a quiet month has no such section.
- The schedule lists what is coming up in the next three months by
  date, soonest first, then what recurs (practice every Tuesday, the
  yearly renewal), each with its source. A date already past, too far
  ahead or not a date at all never reaches the page.
- A section with nothing in it is not on the page, heading included.
- A German household gets German headings.
- An answer the compiler cannot read leaves the page as it was.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

from stacklets.memory.bot.cli import wiki  # noqa: E402
from stack.people import Person  # noqa: E402

PEOPLE = {
    "bart": Person("bart", "Bart Simpson", account="bart"),
    "homer": Person("homer", "Homer Simpson", account="homer"),
    "maggie": Person("maggie", "Maggie Simpson"),
}
ROSTER = ["bart", "homer", "maggie"]
DEED = {"rel": "family/documents/2009/06/deed-p1.md", "type": "document", "title": "Deed",
        "date": "2009-06-15", "summary": "742 Evergreen Terrace, bought by Homer and Marge."}
CAMPING = {"rel": "family/diary/entries/2026/10/2026-10-04-aa11.md", "type": "diary",
           "title": "Camping at the lake", "date": "2026-10-04",
           "summary": "Homer's tent collapsed in the rain; Lisa counted owls."}
INVOICE = {"rel": "family/documents/2026/10/vet-p2.md", "type": "document", "title": "Vet invoice",
           "date": "2026-10-06", "summary": "Santa's Little Helper, vaccination."}
INDEX = [DEED]
TODAY = date(2026, 10, 11)

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
    "glance": [{"label": "Home", "value": "Owned since 2009", "cite": [1]}],
    "pets": [{"name": "Santa's Little Helper", "detail": "our greyhound", "cite": [1]}],
    "lately": "The family went camping at the lake. [2]",
    "insurance": [],
}


class _Model:
    def __init__(self, answer) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def request_key(self, role) -> tuple[str, ...]:
        return ("stand-in",)

    async def complete(self, role, prompt, **kwargs) -> str:
        self.prompts.append(prompt)
        return self.answer if isinstance(self.answer, str) else json.dumps(self.answer)


@pytest.fixture
def brain(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_REPO_DIR", str(tmp_path))
    (tmp_path / "index.md").write_text(
        "---\ntitle: Family Memory\ngenerated: true\n---\n", encoding="utf-8")
    return tmp_path


def _home(brain: Path, answer, lang: str = "en", index: list[dict] = INDEX,
          model: _Model | None = None) -> str:
    rc = asyncio.run(wiki._generate_home(
        model or _Model(answer), index, roster=ROSTER, people=PEOPLE,
        shared_bucket="family", lang=lang, write=True, today=TODAY))
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
        page = _home(brain, {**ANSWER, "glance": []})
        assert "## At a glance" not in page and "## Insurance" not in page

    def test_a_german_household_reads_german_headings(self, brain):
        page = _home(brain, ANSWER, lang="de", index=[DEED, CAMPING])
        assert "## Auf einen Blick" in page and "## Familie" in page and "## Verwandte" in page
        assert "## Freunde und Nachbarn" in page and "## Zuletzt" in page


class TestTheHouseholdAtAGlance:
    def test_the_key_facts_come_right_after_the_welcome(self, brain):
        page = _home(brain, ANSWER)
        assert "## At a glance\n- **Home** · Owned since 2009 [1]" in page
        assert page.index("## At a glance") < page.index("## Family")

    def test_the_pets_are_listed_with_the_family(self, brain):
        page = _home(brain, ANSWER)
        assert ("- **[Maggie Simpson](maggie/about)**\n"
                "- **Santa's Little Helper** · our greyhound. [1]\n") in page


class TestTheLastMonth:
    def test_is_told_right_after_the_welcome(self, brain):
        page = _home(brain, ANSWER, index=[DEED, CAMPING])
        assert "## Lately\nThe family went camping at the lake. [2]" in page
        assert page.index("## Lately") < page.index("## At a glance") < page.index("## Family")

    def test_the_model_reads_only_this_months_diary_and_notes_for_it(self, brain):
        """A deed from 2009 or a vet invoice is not what the family did lately."""
        model = _Model(ANSWER)
        _home(brain, ANSWER, index=[DEED, CAMPING, INVOICE], model=model)
        assert "only the sources 2" in model.prompts[0]

    def test_a_quiet_month_has_no_such_section(self, brain):
        page = _home(brain, ANSWER, index=[DEED])
        assert "## Lately" not in page and "camping" not in page


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
            evidence="", ids=["maggie"], names={"maggie": "Maggie Simpson"}, lang="en", recent=[], today=date(2026, 10, 11),
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


class TestTheSchedule:
    LODGE = {"date": "2026-10-14", "end": "2026-10-16", "what": "Lodge at the park, two rooms", "cite": [1]}
    DENTIST = {"date": "2026-10-12", "what": "Bart at the dentist", "cite": [1]}

    def _schedule(self, brain, *events, lang="en") -> str:
        return _home(brain, {**ANSWER, "schedule": list(events)}, lang=lang)

    def test_lists_what_is_coming_soonest_first(self, brain):
        page = self._schedule(brain, self.LODGE, self.DENTIST)
        assert ("## Schedule\n- **12 Oct 2026** · Bart at the dentist [1]\n"
                "- **14–16 Oct 2026** · Lodge at the park, two rooms [1]\n") in page

    def test_sits_between_the_last_month_and_the_facts(self, brain):
        page = _home(brain, {**ANSWER, "schedule": [self.DENTIST]}, index=[DEED, CAMPING])
        assert page.index("## Lately") < page.index("## Schedule") < page.index("## At a glance")

    def test_what_recurs_follows_the_dates(self, brain):
        band = {"when": "Every Tuesday", "what": "Lisa's band practice", "cite": [1]}
        page = _home(brain, {**ANSWER, "schedule": [self.DENTIST], "recurring": [band]})
        assert ("- **12 Oct 2026** · Bart at the dentist [1]\n"
                "- **Every Tuesday** · Lisa's band practice [1]\n") in page

    def test_what_recurs_is_a_schedule_on_its_own(self, brain):
        rent = {"when": "Monthly", "what": "Mortgage payment", "cite": [1]}
        assert "## Schedule\n- **Monthly** · Mortgage payment [1]" in _home(brain, {**ANSWER, "recurring": [rent]})

    def test_something_still_running_stays(self, brain):
        trip = {"date": "2026-10-09", "end": "2026-10-12", "what": "Camping", "cite": [1]}
        assert "- **9–12 Oct 2026** · Camping [1]" in self._schedule(brain, trip)

    def test_past_far_off_or_guessed_dates_are_left_out(self, brain):
        page = self._schedule(
            brain,
            {"date": "2026-10-01", "what": "Already happened"},
            {"date": "2027-06-01", "what": "Next summer"},
            {"date": "next Friday", "what": "A guess"})
        assert "## Schedule" not in page

    def test_a_german_household_reads_german_dates(self, brain):
        page = self._schedule(brain, self.LODGE, lang="de")
        assert "## Termine\n- **14.–16. Okt. 2026** · Lodge at the park, two rooms [1]" in page

    def test_the_model_knows_what_day_it_is(self, brain):
        model = _Model(ANSWER)
        _home(brain, ANSWER, model=model)
        assert "Today is 2026-10-11." in model.prompts[0]
