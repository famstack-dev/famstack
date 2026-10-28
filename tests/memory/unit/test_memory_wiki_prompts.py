"""The wiki's model prompts, pinned byte for byte.

The home, person and topic pages each ask the model with one prompt.
These tests fill each prompt with fabricated Simpsons inputs, in both
languages and with every optional part on and off, and compare it with
a golden file in `wiki_prompts/`. A change to a prompt's wording or
order is a deliberate edit, so it shows up here as a diff of the golden
files, and a change to how the prompt is assembled must not show up at
all.

To accept a deliberate change, write the golden files again and review
their diff:

    MEMORY_WRITE_GOLDEN=1 uv run --extra test pytest tests/memory/unit/test_memory_wiki_prompts.py
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from stacklets.memory.bot import home_page, person_page, topic_page

GOLDEN = Path(__file__).resolve().parent / "wiki_prompts"
WRITE = os.environ.get("MEMORY_WRITE_GOLDEN") == "1"

TODAY = date(2026, 10, 11)

EVIDENCE = """[1] 2025-11-27 · note · Maggie's first word, filed by Marge
    Maggie said "Daddy" at breakfast. Homer cried.

[2] 2026-09-30 · document · Car insurance renewal, filed by Homer
    Springfield Mutual renews the car insurance for the pink sedan."""

NAMES = {"homer": "Homer Simpson", "marge": "Marge Simpson", "bart": "Bart Simpson",
         "lisa": "Lisa Simpson", "maggie": "Maggie Simpson"}

PREVIOUS = """Maggie is the baby of the family. [1]

## Loves
- Her pacifier. [1]"""


# ── The cases ────────────────────────────────────────────────────────────

def _home(**kw) -> str:
    args = dict(evidence=EVIDENCE, ids=list(NAMES), names=NAMES, lang="en",
                recent=[1, 2], today=TODAY, aliases={"maggie": ["Margaret", "Mags"]})
    return home_page.prompt(**{**args, **kw})


def _person(**kw) -> str:
    args = dict(display="Maggie Simpson", first_name="Maggie", evidence=EVIDENCE,
                facts=[("born", "Maggie was born on 14 January 2025.")], lately=[1],
                lang="en", family=["Homer Simpson (Homer, Dad)", "Maggie Simpson (Margaret)"],
                previous=PREVIOUS, new_evidence=["[2] 2026-09-30 · Car insurance renewal"])
    return person_page.prompt(**{**args, **kw})


def _topic(**kw) -> str:
    args = dict(display="Itchy Scratchy Land", scope="shared", evidence_text=EVIDENCE,
                todos=["Book the lodge", "Ask Grandpa to feed the dog"], lang="en")
    return topic_page.prompt(**{**args, **kw})


CASES = {
    "home.en": lambda: _home(),
    "home.de": lambda: _home(lang="de"),
    "home.no-lately": lambda: _home(recent=[]),
    "home.no-aliases-no-name": lambda: _home(aliases=None, names={**NAMES, "maggie": ""}),
    "person.en": lambda: _person(),
    "person.de": lambda: _person(lang="de"),
    "person.minimal": lambda: _person(facts=[], lately=[], family=None, previous="", new_evidence=None),
    "person.previous-nothing-new": lambda: _person(new_evidence=[]),
    "topic.en": lambda: _topic(),
    "topic.de": lambda: _topic(lang="de"),
    "topic.own-no-todos": lambda: _topic(scope="personal", todos=[]),
}


# ── The test ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("case", sorted(CASES))
def test_the_prompt_is_what_the_model_reads(case):
    prompt = CASES[case]()
    path = GOLDEN / f"{case}.txt"
    if WRITE:
        path.parent.mkdir(exist_ok=True)
        path.write_text(prompt, encoding="utf-8")
    assert prompt == path.read_text(encoding="utf-8")
