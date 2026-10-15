"""A person's page: the template holds the layout, the model writes the prose.

Read from the outside, the way a family member reads the published page.
What these tests promise (docs/design/brain/wiki-page-anatomy.md):

- Dates, the latest entries and the documents come from what was filed,
  so a page never shows a made-up date or a diary entry as a document.
- The facts a relative looks up (born, school or job, doctor) sit right
  under the About text, each with its source.
- A section with nothing in it is not on the page at all, heading
  included. No "nothing recorded".
- A German household gets German headings and German dates.
- The model may add one section of its own; without it there is none.
- An answer the compiler cannot read leaves the page as it was.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot" / "cli"))

import wiki  # noqa: E402
from stack.people import Person  # noqa: E402

MAGGIE = Person("maggie", "Maggie Simpson", ("Margaret",))

FIRST_WORD = {"rel": "family/diary/entries/2025/11/2025-11-27-aa11.md", "type": "diary",
              "title": "Maggie's first word", "date": "2025-11-27", "persons": ["maggie"],
              "summary": "At Thanksgiving Maggie said 'turkey'."}
SQUISHEE = {"rel": "family/diary/entries/2024/08/2024-08-24-bb22.md", "type": "diary",
            "title": "Blue Squishee face", "date": "2024-08-24", "persons": ["maggie"],
            "summary": "Apu gave Maggie a free Squishee; her face turned blue."}
BIRTH = {"rel": "family/documents/2025/01/2025-01-18-birth-certificate-p47.md",
         "type": "document", "title": "Birth Certificate Margaret Simpson",
         "date": "2025-01-18", "persons": ["maggie", "homer", "marge"],
         "summary": "Birth certificate of Margaret Simpson."}


class _Model:
    def __init__(self, answer: dict | str) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    async def complete(self, role, prompt, **kwargs) -> str:
        self.prompts.append(prompt)
        return self.answer if isinstance(self.answer, str) else json.dumps(self.answer)


ANSWER = {
    "tagline": "The youngest Simpson, rarely without her pacifier.",
    "about": "Maggie is the baby of the family and watches everything. [1]",
    "loves": ["Squishees, even when they turn her blue. [2]"],
    "lately": {"1": "Said her first word at Thanksgiving: turkey",
               "2": "Got a free Squishee from Apu and turned blue"},
}


@pytest.fixture
def brain(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_REPO_DIR", str(tmp_path))
    (tmp_path / "maggie").mkdir()
    return tmp_path


def _page(brain: Path, answer, entries, lang: str = "en") -> str:
    rc = asyncio.run(wiki._generate_member(
        _Model(answer), "maggie", entries, brain, person=MAGGIE,
        shared_bucket="family", lang=lang, write=True))
    assert rc == 0
    return (brain / "maggie" / "about.md").read_text(encoding="utf-8")


class TestWhatTheCompilerFillsIn:
    def test_latest_entries_carry_their_own_dates_newest_first(self, brain):
        page = _page(brain, ANSWER, [FIRST_WORD, SQUISHEE])
        lately = page.split("## Lately\n", 1)[1].split("\n\n", 1)[0].splitlines()
        assert lately == [
            "- **27 Nov 2025** · Said her first word at Thanksgiving: turkey. [1]",
            "- **24 Aug 2024** · Got a free Squishee from Apu and turned blue. [2]",
        ]

    def test_an_entry_the_model_wrote_no_line_for_keeps_its_title(self, brain):
        answer = {**ANSWER, "lately": {"1": "Said 'turkey'"}}
        page = _page(brain, answer, [FIRST_WORD, SQUISHEE])
        assert "- **24 Aug 2024** · Blue Squishee face. [2]" in page

    def test_documents_lists_only_what_was_filed_as_a_document(self, brain):
        page = _page(brain, ANSWER, [FIRST_WORD, SQUISHEE, BIRTH])
        documents = page.split("## Documents\n", 1)[1].split("\n\n", 1)[0]
        assert documents == (
            "- [Birth Certificate Margaret Simpson]"
            "(/family/documents/2025/01/2025-01-18-birth-certificate-p47.md) · 18 Jan 2025")


class TestWhatIsStillToDo:
    """The list items that are Maggie's, from every list, soonest due first."""

    LISTS = {
        "schule": ("---\ntype: list\ntitle: Schule\n---\n\n"
                   "- [ ] Vaccination card [Maggie](../people/maggie.md)\n"
                   "- [ ] Field trip form [Maggie](../people/maggie.md) 📅 2026-10-20\n"
                   "- [x] Lunch money [Maggie](../people/maggie.md) 📅 2026-10-01\n"),
        "einkauf": ("---\ntype: list\ntitle: Einkauf\n---\n\n"
                    "- [ ] Pacifier [Maggie](../people/maggie.md) 📅 2026-10-14\n"
                    "- [ ] Duff [Homer](../people/homer.md)\n"),
    }

    def _with_lists(self, brain):
        (brain / "family" / "lists").mkdir(parents=True)
        for slug, text in self.LISTS.items():
            (brain / "family" / "lists" / f"{slug}.md").write_text(text, encoding="utf-8")

    def test_open_ones_only_soonest_due_first_each_with_its_list(self, brain):
        self._with_lists(brain)
        page = _page(brain, ANSWER, [FIRST_WORD])
        assert page.split("## Still to do\n", 1)[1].split("\n\n", 1)[0] == (
            "- [ ] Pacifier · 📅 14 Oct 2026 · [Einkauf](/family/lists/einkauf.md)\n"
            "- [ ] Field trip form · 📅 20 Oct 2026 · [Schule](/family/lists/schule.md)\n"
            "- [ ] Vaccination card · [Schule](/family/lists/schule.md)")

    def test_it_sits_under_the_facts_at_a_glance(self, brain):
        self._with_lists(brain)
        page = _page(brain, ANSWER, [FIRST_WORD], lang="de")
        assert "## Noch zu tun\n- [ ] Pacifier · 📅 14. Okt. 2026" in page

    def test_nothing_open_for_her_no_heading(self, brain):
        page = _page(brain, ANSWER, [FIRST_WORD])
        assert "## Still to do" not in page


class TestAnEmptySectionIsNotThere:
    def test_no_documents_no_documents_heading(self, brain):
        page = _page(brain, ANSWER, [FIRST_WORD, SQUISHEE])
        assert "## Documents" not in page

    def test_nothing_loved_no_loves_heading(self, brain):
        page = _page(brain, {**ANSWER, "loves": []}, [FIRST_WORD, SQUISHEE])
        assert "loves" not in page.split("<!-- end: generated -->")[0].lower()

    def test_no_tagline_no_empty_quote(self, brain):
        page = _page(brain, {**ANSWER, "tagline": ""}, [FIRST_WORD])
        assert "\n>" not in page.split("<!-- begin: generated -->", 1)[1]

    def test_the_templates_own_notes_are_not_published(self, brain):
        page = _page(brain, ANSWER, [FIRST_WORD])
        assert "Slots in double braces" not in page and "{{" not in page


class TestTheFactsAtAGlance:
    FACTS = {**ANSWER, "facts": [{"label": "Born", "value": "18 January 2025", "cite": [2]},
                                 {"label": "Doctor", "value": "Dr. Hibbert"}]}

    def test_sit_under_the_about_text_with_their_sources(self, brain):
        page = _page(brain, self.FACTS, [FIRST_WORD, BIRTH])
        assert ("watches everything. [1]\n\n## At a glance\n"
                "- **Born** · 18 January 2025 [2]\n- **Doctor** · Dr. Hibbert\n\n"
                "## What Maggie loves") in page

    def test_a_german_household_reads_a_german_heading(self, brain):
        assert "## Auf einen Blick\n- **Born**" in _page(brain, self.FACTS, [FIRST_WORD, BIRTH], lang="de")


class TestAGermanHousehold:
    def test_headings_and_dates_are_german(self, brain):
        page = _page(brain, ANSWER, [FIRST_WORD, BIRTH], lang="de")
        assert "## Was Maggie liebt" in page
        assert "## Zuletzt" in page and "## Dokumente" in page
        assert "## Quellen" in page and "## References" not in page
        assert "- **27. Nov. 2025** · " in page

    def test_the_model_is_asked_to_write_german(self, brain):
        model = _Model(ANSWER)
        asyncio.run(wiki._generate_member(
            model, "maggie", [FIRST_WORD], brain, person=MAGGIE,
            shared_bucket="family", lang="de", write=True))
        assert "Write in German" in model.prompts[0]


class TestASectionOfTheModelsOwn:
    def test_it_appears_under_the_heading_the_model_chose(self, brain):
        answer = {**ANSWER, "more": {"heading": "Firsts", "text": "Her first word was 'turkey'. [1]"}}
        page = _page(brain, answer, [FIRST_WORD])
        assert "## Firsts\nHer first word was 'turkey'. [1]" in page

    def test_without_one_there_is_none(self, brain):
        page = _page(brain, ANSWER, [FIRST_WORD])
        headings = [ln for ln in page.splitlines() if ln.startswith("## ")]
        assert headings == ["## What Maggie loves", "## Lately", "## References"]


class TestWhatTheModelIsTold:
    """What the prompt carries so the prose credits the right person."""

    MARGE = Person("marge", "Marge Simpson", account="marge")

    def _prompt(self, brain, entries) -> str:
        model = _Model(ANSWER)
        asyncio.run(wiki._generate_member(
            model, "maggie", entries, brain, person=MAGGIE,
            people={"maggie": MAGGIE, "marge": self.MARGE},
            shared_bucket="family", lang="en", write=True))
        return model.prompts[0]

    def test_who_filed_each_source(self, brain):
        """Without it Homer's page says he booked the lodge Marge booked."""
        entry = {**FIRST_WORD, "filed_by": "marge"}
        assert "[1] 2025-11-27 · Maggie's first word, filed by Marge" in self._prompt(brain, [entry])

    def test_every_name_a_family_member_goes_by(self, brain):
        """Without it a birth certificate for Margaret reads as a new daughter."""
        assert "- Maggie Simpson (also called Margaret)" in self._prompt(brain, [BIRTH])


class TestAnAnswerThatCannotBeRead:
    def test_the_page_stays_as_it_was(self, brain):
        before = _page(brain, ANSWER, [FIRST_WORD])
        rc = asyncio.run(wiki._generate_member(
            _Model("Here is Maggie's page: ..."), "maggie", [FIRST_WORD, SQUISHEE], brain,
            person=MAGGIE, shared_bucket="family", lang="en", write=True))
        assert rc == 1
        assert (brain / "maggie" / "about.md").read_text(encoding="utf-8") == before
