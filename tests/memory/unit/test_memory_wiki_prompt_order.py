"""The wiki's prompts grow at the end, so the model server reuses what it read.

oMLX reuses its cache up to the first token where a prompt differs from
one it read before. A page rebuild after one new filing should therefore
send the previous prompt again up to the new filing, and only then
something new. What these tests promise, for the home page and a
person's page, compiled through `wiki.run` over a brain that is a git
repository as the curator keeps it:

- The sources are in the order they were filed: a new filing comes
  last, whatever date it carries and wherever its path sorts. At the
  compile right after a filing the curator has not committed it yet; at
  the next one it has, and it stays last.
- Today's date comes after the sources, so a rebuild of an unchanged
  memory on another day repeats everything up to it.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import date
from pathlib import Path

import pytest

from stacklets.memory.bot.cli import wiki

PEOPLE = {
    "family/people/homer.md": "---\ntype: person\ncard_id: person-homer\ntitle: Homer Simpson\n"
                              "account: homer\n---\n\n# Homer Simpson\n",
    "family/people/marge.md": "---\ntype: person\ncard_id: person-marge\ntitle: Marge Simpson\n"
                              "account: marge\n---\n\n# Marge Simpson\n",
}


def _document(card_id: str, title: str, day: str, summary: str) -> str:
    return (f"---\ntype: document\ncard_id: {card_id}\ntitle: {title}\ndate: \"{day}\"\n"
            f"persons:\n  - homer\n---\n\n# {title}\n\n> [!summary]\n> {summary}\n")


# Filed in this order, one commit each. Their paths and dates sort differently.
FILED = [
    ("family/documents/2026/09/plant-p3.md",
     _document("document-a1", "Power plant contract", "2026-09-01", "Homer's job at the plant.")),
    ("family/documents/2026/10/duff-p7.md",
     _document("document-b2", "Duff Insurance policy", "2026-10-02", "The policy covers the car.")),
    ("family/documents/2026/03/school-p5.md",
     _document("document-c3", "School report", "2026-03-20", "Bart's report, signed by Homer.")),
]

# Filed last: an old letter, so its date and its path both sort first.
NEW = ("family/documents/2009/06/deed-p1.md",
       _document("document-d4", "House deed", "2009-06-15", "742 Evergreen Terrace, bought by Homer."))


class _Model:
    """Records every prompt; answers with an empty object, which the pages accept."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def request_key(self, role) -> tuple[str, ...]:
        return ("stand-in",)

    async def complete(self, role, prompt, **kwargs) -> str:
        self.prompts.append(prompt)
        return "{}"


def _git(brain: Path, *args: str, when: str = "") -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else None
    subprocess.run(["git", "-C", str(brain), "-c", "user.name=curator",
                    "-c", "user.email=curator@example.invalid", *args],
                   check=True, capture_output=True, env=env)


def _file(brain: Path, rel: str, text: str) -> None:
    path = brain / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def brain(tmp_path, monkeypatch):
    """The brain after three filings, one curator commit each, a day apart."""
    monkeypatch.setenv("BRAIN_REPO_DIR", str(tmp_path))
    monkeypatch.setenv("SHARED_BUCKET", "family")
    monkeypatch.setenv("LANGUAGE", "en")
    _git(tmp_path, "init", "-q")
    for rel, text in PEOPLE.items():
        _file(tmp_path, rel, text)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "people", when="2026-09-01T08:00:00+00:00")
    for day, (rel, text) in enumerate(FILED, start=2):
        _file(tmp_path, rel, text)
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-q", "-m", rel, when=f"2026-09-{day:02d}T08:00:00+00:00")
    return tmp_path


def _prompt(argv: list[str], today: date, monkeypatch) -> str:
    """The prompt `wiki.run` sends for the one page `argv` selects, on `today`."""
    class _Today(date):
        @classmethod
        def today(cls):
            return today

    monkeypatch.setattr(wiki, "date", _Today)
    model = _Model()
    assert asyncio.run(wiki.run(model, [*argv, "--dry-run"])) == 0
    assert len(model.prompts) == 1
    return model.prompts[0]


def _sources_and_before(prompt: str, after_sources: str) -> str:
    """The prompt up to the line that follows its sources."""
    return prompt[:prompt.index(after_sources)]


PAGES = [
    pytest.param(["--home"], "\nToday is ", id="home"),
    pytest.param(["--member", "homer"], "\nWrite in ", id="person"),
]


@pytest.mark.parametrize("argv, after_sources", PAGES)
class TestANewFilingIsReadLast:
    def test_the_previous_prompt_up_to_the_new_filing_is_sent_again(
            self, brain, monkeypatch, argv, after_sources):
        day = date(2026, 10, 11)
        before = _sources_and_before(_prompt(argv, day, monkeypatch), after_sources)
        assert all(title_of(text) in before for _, text in FILED)

        _file(brain, *NEW)  # mirrored in, not committed yet
        after = _prompt(argv, day, monkeypatch)
        assert after.startswith(before)
        assert "House deed" in _sources_and_before(after, after_sources)[len(before):]

    def test_it_stays_last_once_the_curator_has_committed_it(
            self, brain, monkeypatch, argv, after_sources):
        day = date(2026, 10, 11)
        _file(brain, *NEW)
        uncommitted = _sources_and_before(_prompt(argv, day, monkeypatch), after_sources)
        _git(brain, "add", "-A")
        _git(brain, "commit", "-q", "-m", "deed", when="2026-10-10T08:00:00+00:00")
        assert _sources_and_before(_prompt(argv, day, monkeypatch), after_sources) == uncommitted


def test_an_unchanged_memory_on_another_day_repeats_the_home_prompt_up_to_the_date(
        brain, monkeypatch):
    first = _prompt(["--home"], date(2026, 10, 11), monkeypatch)
    second = _prompt(["--home"], date(2026, 10, 12), monkeypatch)
    stable = _sources_and_before(first, "\nToday is ")
    assert all(title_of(text) in stable for _, text in FILED)
    assert second.startswith(stable)


def title_of(card: str) -> str:
    return card.split("title: ", 1)[1].split("\n", 1)[0]


# Filed in one burst, a minute apart, and committed by the curator in one
# commit. Record ids are random, so these sort the other way round.
BURST = [
    ("family/documents/2026/10/water-p8.md",
     _document("document-f9", "Water bill", "2026-10-03", "Springfield Water, paid by Homer.")
     .replace("persons:", "timestamp: \"2026-10-10T08:00:00Z\"\npersons:")),
    ("family/documents/2026/10/power-p9.md",
     _document("document-a0", "Power bill", "2026-10-04", "Springfield Power, paid by Homer.")
     .replace("persons:", "timestamp: \"2026-10-10T08:01:00Z\"\npersons:")),
]


@pytest.mark.parametrize("argv, after_sources", PAGES)
def test_a_burst_keeps_its_order_once_the_curator_commits_it(
        brain, monkeypatch, argv, after_sources):
    """Given two cards filed in one burst, when the curator commits them in
    one commit, then the prompt numbers them as it did before the commit:
    in the order they were filed, not in the order of their ids."""
    day = date(2026, 10, 11)
    for rel, text in BURST:
        _file(brain, rel, text)
    uncommitted = _sources_and_before(_prompt(argv, day, monkeypatch), after_sources)
    assert uncommitted.index("Water bill") < uncommitted.index("Power bill")

    _git(brain, "add", "-A")
    _git(brain, "commit", "-q", "-m", "bills", when="2026-10-10T08:02:00+00:00")

    assert _sources_and_before(_prompt(argv, day, monkeypatch), after_sources) == uncommitted
