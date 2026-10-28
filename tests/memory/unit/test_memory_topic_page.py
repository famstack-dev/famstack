"""A topic's page: the facts of the topic and where it stands.

Read from the outside, as the family reads the published page. What
these tests promise:

- The open items are the family's own lists naming the topic, exactly,
  under each list's name, and done items are not shown.
- Everything filed under the topic is listed under its kind with who
  filed it and when, newest first; what was filed elsewhere has a
  section of its own.
- The model's facts and state are shown with their citations; a section
  with nothing in it is not on the page.
- The model is told who filed each source, so it can say who did what.
- A German household gets German headings; an unreadable answer leaves
  the page as it was.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

from stacklets.memory.bot.cli import wiki  # noqa: E402
from stack.people import Person  # noqa: E402

PEOPLE = {"marge": Person("marge", "Marge Simpson", account="marge"),
          "homer": Person("homer", "Homer Simpson", account="homer")}

PLAN = {"rel": "family/trip/notes/2026/10/plan-1.md", "title": "Road trip plan",
        "date": "2026-10-09", "filed_by": "homer", "summary": "Leave Friday after work."}
LODGE = {"rel": "family/trip/notes/2026/10/lodge-2.md", "title": "Lodge booked",
         "date": "2026-10-10", "filed_by": "marge", "summary": "Two rooms, 14 to 16 October."}
PARK = {"rel": "family/trip/bookmarks/2026/10/park-3.md", "title": "The park",
        "date": "2026-10-08", "filed_by": "homer", "summary": "Encyclopedia page."}
POLICY = {"rel": "family/documents/2026/03/travel-insurance-p9.md", "title": "Travel insurance",
          "date": "2026-03-15", "filed_by": "", "summary": "Covers trips abroad.",
          "topics": ["trip"], "tags": []}

TODOS = ("---\ntype: list\ntitle: Trip\ntopics:\n  - trip\n---\n\n"
         "- [ ] Pack sunscreen\n- [x] Book the lodge\n- [ ] Find a dog sitter\n")

ANSWER = {
    "title": "The Big Trip",
    "summary": "The family's October road trip.",
    "facts": [{"label": "Dates", "value": "14 to 16 October", "cite": [2]}],
    "state": [{"text": "Marge booked the lodge, two rooms", "cite": [2]}],
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
    (tmp_path / "family" / "trip").mkdir(parents=True)
    (tmp_path / "family" / "lists").mkdir(parents=True)
    (tmp_path / "family" / "lists" / "trip.md").write_text(TODOS, encoding="utf-8")
    return tmp_path


def _topic(brain: Path, answer, lang: str = "en", model: _Model | None = None) -> str:
    model = model or _Model(answer)
    rc = asyncio.run(wiki._generate_topic(
        model, "family", "trip", [PLAN, LODGE, PARK, POLICY], people=PEOPLE,
        shared_bucket="family", lang=lang, write=True))
    assert rc == 0
    return (brain / "family" / "trip" / "about.md").read_text(encoding="utf-8")


def _section(page: str, heading: str) -> str:
    return page.split(f"## {heading}\n", 1)[1].split("\n\n", 1)[0]


class TestWhatTheCompilerLists:
    def test_the_open_items_under_their_lists_name_as_the_family_keeps_them(self, brain):
        assert _section(_topic(brain, ANSWER), "Still to do") == (
            "**[Trip](/family/lists/trip.md)**\n- [ ] Pack sunscreen\n- [ ] Find a dog sitter")

    def test_every_list_naming_the_topic_with_who_and_by_when(self, brain):
        (brain / "family" / "lists" / "packing.md").write_text(
            "---\ntype: list\ntitle: Packing\ntopics:\n  - trip\n---\n\n"
            "- [ ] Tent [Homer](../people/homer.md) 📅 2026-10-13\n", encoding="utf-8")
        page = _topic(brain, ANSWER)
        still = page.split("## Still to do\n", 1)[1].split("\n## ", 1)[0].strip()
        assert still == (
            "**[Packing](/family/lists/packing.md)**\n- [ ] Tent · Homer · 📅 13 Oct 2026\n\n"
            "**[Trip](/family/lists/trip.md)**\n- [ ] Pack sunscreen\n- [ ] Find a dog sitter")

    def test_the_model_reads_the_open_items_but_does_not_write_them(self, brain):
        model = _Model(ANSWER)
        _topic(brain, ANSWER, model=model)
        assert "Pack sunscreen" in model.prompts[0] and "Book the lodge" not in model.prompts[0]

    def test_notes_newest_first_with_who_and_when(self, brain):
        assert _section(_topic(brain, ANSWER), "Notes") == (
            "- [Lodge booked](/family/trip/notes/2026/10/lodge-2.md) · Marge · 10 Oct 2026\n"
            "- [Road trip plan](/family/trip/notes/2026/10/plan-1.md) · Homer · 9 Oct 2026")

    def test_links_and_what_was_filed_elsewhere_have_their_own_sections(self, brain):
        page = _topic(brain, ANSWER)
        assert _section(page, "Links").startswith("- [The park](")
        assert _section(page, "Elsewhere in the wiki").startswith("- [Travel insurance](")


class TestWhatTheModelReads:
    def test_facts_and_state_with_their_citations(self, brain):
        page = _topic(brain, ANSWER)
        assert page.split("<!-- begin: generated -->", 1)[1].lstrip().startswith(
            "# The Big Trip\n> The family's October road trip.")
        assert _section(page, "At a glance") == "- **Dates** · 14 to 16 October [2]"
        assert _section(page, "Where things stand") == "- Marge booked the lodge, two rooms. [2]"

    def test_nothing_to_say_no_section(self, brain):
        page = _topic(brain, {"summary": "A trip."})
        assert "## At a glance" not in page and "## Where things stand" not in page

    def test_the_model_knows_who_filed_each_source(self, brain):
        """Without it a booking Marge made is credited to Homer."""
        model = _Model(ANSWER)
        _topic(brain, ANSWER, model=model)
        assert "[2] 2026-10-10 · note · Lodge booked, filed by Marge" in model.prompts[0]


class TestLanguageAndFailure:
    def test_a_german_household_reads_german_headings(self, brain):
        page = _topic(brain, ANSWER, lang="de")
        for heading in ("Auf einen Blick", "Stand der Dinge", "Noch zu tun", "Notizen",
                        "Anderswo im Wiki", "Quellen"):
            assert f"## {heading}\n" in page

    def test_an_unreadable_answer_keeps_the_page(self, brain):
        before = _topic(brain, ANSWER)
        rc = asyncio.run(wiki._generate_topic(
            _Model("The trip is going well."), "family", "trip", [PLAN, LODGE], people=PEOPLE,
            shared_bucket="family", lang="en", write=True))
        assert rc == 1
        assert (brain / "family" / "trip" / "about.md").read_text(encoding="utf-8") == before
