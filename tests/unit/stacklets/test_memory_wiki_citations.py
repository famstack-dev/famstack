"""A rebuilt person page keeps every citation on its own source.

A person page is rebuilt from the previous version (the baseline) so a
new filing changes a few lines instead of the whole page. New evidence
shifts the numbering, so the compiler renumbers the baseline's `[N]`
citations to the current order before the model sees it, and the prompt
promises the model that they already match. These tests hold the
compiler to that promise from the outside: rebuild a page after a new
diary entry, with a model that keeps the baseline as it is, and read
the published page the way a family member does, sentence then
reference.

Diary entries are the hard case: the References block links each one
to its month page, so several entries share one link.
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

HOMER = Person("homer", "Homer Simpson", account="homer")


def _entry(rel: str, title: str, date: str) -> dict:
    return {"rel": rel, "title": title, "date": date, "persons": ["homer"],
            "summary": f"{title}, as Homer tells it."}


PANCAKES = _entry("family/diary/entries/2026/02/2026-02-14-aa11.md",
                  "Heart-shaped pancakes", "2026-02-14")
SLEDDING = _entry("family/diary/entries/2026/02/2026-02-21-bb22.md",
                  "Sledding on Mount Springfield", "2026-02-21")
INSURANCE = _entry("family/documents/2026/03/2026-03-15-duff-insurance-p34.md",
                   "Duff Insurance renewal", "2026-03-15")
# Filed later, but dated earlier: it lands first in the evidence order.
HALLOWEEN = _entry("family/diary/entries/2025/10/2025-10-31-cc33.md",
                   "Halloween costumes", "2025-10-31")


class _Model:
    """Writes one "loves" line per source the first time, then keeps the page.

    Keeping the page's wording is what the prompt asks for when nothing
    contradicts it, and what a model at temperature 0 mostly does.
    Every number on the page is therefore the compiler's.
    """

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, role, prompt, **kwargs) -> str:
        self.prompts.append(prompt)
        if "THE PAGE AS IT IS NOW:\n" in prompt:
            page = prompt.split("THE PAGE AS IT IS NOW:\n", 1)[1]
            loves = re.findall(r"^- ([^*].*\[\d+\])$", page.split("## Lately")[0], re.M)
        else:
            loves = [f"{title}. [{n}]" for n, title in
                     re.findall(r"^\[(\d+)\] [\d-]+ · (.+?)(?:  \(|$)", prompt, re.M)]
        return json.dumps({"tagline": "Dad.", "about": "Homer is Homer.", "loves": loves})


@pytest.fixture
def brain(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_REPO_DIR", str(tmp_path))
    (tmp_path / "homer").mkdir()
    return tmp_path


def _build(model: _Model, brain: Path, entries: list[dict]) -> str:
    rc = asyncio.run(wiki._generate_member(
        model, "homer", entries, brain, person=HOMER,
        shared_bucket="family", lang="en", write=True))
    assert rc == 0
    return (brain / "homer" / "about.md").read_text(encoding="utf-8")


def _cited(page: str) -> dict[str, str]:
    """Each line the model wrote, mapped to the title its reference names."""
    refs = dict(re.findall(r"^- \[(\d+)\] \[([^\]]+)\]", page, re.M))
    loves = page.split("## What Homer loves", 1)[1].split("\n## ", 1)[0]
    return {
        sentence: refs.get(n, f"(no reference {n})")
        for sentence, n in re.findall(r"^- (.+?)\. \[(\d+)\]$", loves, re.M)
    }


class TestARebuildKeepsEveryCitationOnItsSource:
    def test_a_new_diary_entry_does_not_shift_the_others(self, brain):
        model = _Model()
        _build(model, brain, [PANCAKES, SLEDDING, INSURANCE])

        page = _build(model, brain, [HALLOWEEN, PANCAKES, SLEDDING, INSURANCE])

        assert _cited(page) == {
            "Heart-shaped pancakes": "Heart-shaped pancakes",
            "Sledding on Mount Springfield": "Sledding on Mount Springfield",
            "Duff Insurance renewal": "Duff Insurance renewal",
        }

    def test_only_the_new_entry_is_offered_as_new(self, brain):
        """The model is told what to work in. Listing sources the page
        already cites as new invites it to mention them twice."""
        model = _Model()
        _build(model, brain, [PANCAKES, SLEDDING, INSURANCE])

        _build(model, brain, [HALLOWEEN, PANCAKES, SLEDDING, INSURANCE])

        new = model.prompts[-1].split("New since the page was written:", 1)[1]
        new = new.split("THE PAGE AS IT IS NOW", 1)[0]
        assert re.findall(r"^\[\d+\] .+$", new, re.M) == ["[1] 2025-10-31 · Halloween costumes"]

    def test_a_rebuild_with_nothing_new_changes_no_number(self, brain):
        """The resting state: same evidence, same page."""
        model = _Model()
        first = _build(model, brain, [PANCAKES, SLEDDING, INSURANCE])

        assert _build(model, brain, [PANCAKES, SLEDDING, INSURANCE]) == first
        assert "Nothing is new since then." in model.prompts[-1]


class TestAPageFromBeforeTheFixIsRebuiltOnce:
    """Pages compiled before the fix may already cite the wrong diary
    entry in their text, and a baseline rebuild would keep that text.
    Such a page is rebuilt from the evidence alone, once."""

    OLD_PAGE = (
        "---\ntitle: Homer Simpson\nslug: homer\ntype: person\ngenerated: true\n---\n\n"
        "<!-- begin: generated -->\n\n"
        "# Homer Simpson\n\n## Recent Activity\n- Heart-shaped pancakes. [1]\n\n"
        "## References\n\n"
        "- [1] [Sledding on Mount Springfield](/family/diary/2026/02.md) - 2026-02-21\n\n"
        "<!-- end: generated -->\n"
    )

    def test_the_old_text_is_not_offered_as_a_baseline(self, brain):
        (brain / "homer" / "about.md").write_text(self.OLD_PAGE, encoding="utf-8")
        model = _Model()

        page = _build(model, brain, [PANCAKES, SLEDDING])

        assert "THE PAGE AS IT IS NOW" not in model.prompts[-1]
        assert _cited(page) == {
            "Heart-shaped pancakes": "Heart-shaped pancakes",
            "Sledding on Mount Springfield": "Sledding on Mount Springfield",
        }

    def test_the_rebuilt_page_is_a_baseline_again(self, brain):
        (brain / "homer" / "about.md").write_text(self.OLD_PAGE, encoding="utf-8")
        model = _Model()
        _build(model, brain, [PANCAKES, SLEDDING])

        _build(model, brain, [PANCAKES, SLEDDING])

        assert "THE PAGE AS IT IS NOW" in model.prompts[-1]
