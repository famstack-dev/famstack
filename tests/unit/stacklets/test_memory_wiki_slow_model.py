"""A slow or failing model costs one wiki page, never the whole rebuild.

The wiki asks the model once per page, and the AI server is shared with
everything else the household runs. A call that waits past the client's
timeout, or a model that will not stop writing, must leave that page as
it was and let the rebuild go on to the next one: the nightly sweep
covers every page in one run, and a run that stops at the first slow
page leaves all the pages after it stale.

The vault and the brain are real files in a temporary directory; only
the model is a stand-in, since it is the boundary under test.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

from stacklets.memory.bot.cli import wiki  # noqa: E402
from stack.ai.client import LLMTimeoutError  # noqa: E402

HOMER = "---\ntype: person\ntitle: Homer Simpson\naccount: homer\n---\n"
MARGE = "---\ntype: person\ntitle: Marge Simpson\naccount: marge\n---\n"


def _entry(person: str, title: str) -> dict:
    return {"rel": f"{person}/notes/2026-10-01-{person}.md", "title": title,
            "date": "2026-10-01", "persons": [person], "summary": title}


class _Model:
    """Answers every page, except that it never answers in time for `stalls_on`."""

    def __init__(self, stalls_on: str = "") -> None:
        self.stalls_on = stalls_on
        self.calls: list[dict] = []

    async def complete(self, role, prompt, **kwargs) -> str:
        self.calls.append(kwargs)
        if self.stalls_on and f"This page is about: {self.stalls_on}." in prompt:
            raise LLMTimeoutError("Qwen3.6-35B-A3B — model may still be loading, try again")
        return json.dumps({"tagline": "Family.", "about": "Lives in Springfield.", "loves": []})


@pytest.fixture
def simpsons(tmp_path, monkeypatch):
    """Homer and Marge, each with one note of their own."""
    vault, brain = tmp_path / "vault", tmp_path / "brain"
    for person in ("homer", "marge"):
        (vault / person).mkdir(parents=True)
        (brain / person).mkdir(parents=True)
    people = vault / "family" / "people"
    people.mkdir(parents=True)
    (people / "homer.md").write_text(HOMER)
    (people / "marge.md").write_text(MARGE)
    monkeypatch.setenv("MEMORY_VAULT_DIR", str(vault))
    monkeypatch.setenv("BRAIN_REPO_DIR", str(brain))
    index = [_entry("homer", "Donut run"), _entry("marge", "Bowling night")]
    monkeypatch.setattr(wiki, "_index_vault", lambda vault: index)
    monkeypatch.setattr(wiki, "_brain_dir", lambda: brain)
    return brain


class TestAPageTheModelDoesNotAnswerInTime:
    def test_the_next_page_is_still_rebuilt(self, simpsons):
        asyncio.run(wiki.run(_Model(stalls_on="Homer Simpson"),
                             ["--member", "homer", "--member", "marge"]))

        assert "Lives in Springfield." in (simpsons / "marge" / "about.md").read_text()

    def test_its_own_page_stays_as_it_was(self, simpsons):
        before = "---\ntitle: Homer Simpson\ngenerated: true\n---\n\nHomer, as of last night.\n"
        (simpsons / "homer" / "about.md").write_text(before)

        asyncio.run(wiki.run(_Model(stalls_on="Homer Simpson"),
                             ["--member", "homer", "--member", "marge"]))

        assert (simpsons / "homer" / "about.md").read_text() == before

    def test_the_rebuild_still_counts_as_done(self, simpsons):
        """The curator only logs a failed run; the page waits for the next one."""
        rc = asyncio.run(wiki.run(_Model(stalls_on="Homer Simpson"),
                                  ["--member", "homer", "--member", "marge"]))

        assert rc == 0


class TestAModelThatWillNotStop:
    def test_every_page_asks_for_an_answer_of_bounded_length(self, simpsons):
        """Without a cap, a model caught in a loop writes until the client
        timeout (15 minutes) and holds the shared AI server all that time."""
        model = _Model()

        asyncio.run(wiki.run(model, ["--member", "homer", "--member", "marge"]))

        assert model.calls
        assert all(call.get("max_tokens") for call in model.calls)
