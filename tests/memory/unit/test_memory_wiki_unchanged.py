"""A wiki page whose input did not change costs no model call.

The nightly sweep compiles every page. Most nights most pages read the
same cards as the night before, and a model at temperature 0 asked the
same question gives the same answer. So the compile asks the model only
for a page whose request changed: the AI server, the model, the settings,
the prompt the cards make, and the sources the answer cites. An unchanged
page renders from the answer it keeps, as `--fast` renders it, and the
compile says it was not asked.

The compile runs as the bot runner's `wiki` command runs it: `wiki.run`
with the model client, over a brain that holds the Simpsons' cards as the
curator's mirror leaves them. The day is set with `STACK_TODAY`, which
the compile reads through `stack.clock`; the home page's prompt carries
it. Which pages the model was asked for is read
from the compile's record of its calls (`WIKI_CALLS_LOG`). What stands in
is the model, an HTTP server answering the chat completions API (`Model`).

A case takes 0.3 to 0.7 seconds (measured): one compile on Monday, then
the one under test.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
from datetime import date
from pathlib import Path

import pytest
from openai import AsyncOpenAI

from stack.ai import models
from stack.ai.client import LLM
from stacklets.memory.bot import person_page
from stacklets.memory.bot.cli import wiki
from tests.memory.unit.instance import views
from tests.model import Model
from tests.memory.unit.simpsons_cards import ONTOLOGY, VAULT

MONDAY, TUESDAY = date(2026, 10, 26), date(2026, 10, 27)

# One answer every page accepts: the home page's fields, a person's and a
# topic's. It cites the first two sources, so a page with two keeps both.
ANSWER = {"family": "The Simpsons", "welcome": "A loud, loving family. [1]",
          "order": [], "people": {}, "tagline": "A Simpson.",
          "about": "Lives in Springfield. [1] [2]", "summary": "As the cards say. [1]"}

# Marge's two notes, which her page and the home page read, and a note
# filed under the Itchy & Scratchy Land topic, which gives it a page.
ART = "marge/notes/2026/10/art-supplies.md"
EASEL = "marge/notes/2026/10/easel.md"
TICKETS = "family/itchy-scratchy-land/notes/2026/10/tickets.md"
MARGE, BART = "marge/about.md", "bart/about.md"
TOPIC = "family/itchy-scratchy-land/about.md"
EVERY_PAGE = sorted(["index.md", MARGE, BART, TOPIC])


def _note(card_id: str, title: str, on: str, persons: str, body: str) -> str:
    return (f"---\ntype: note\ncard_id: {card_id}\ntitle: {title}\ndate: \"{on}\"\n"
            f"persons:\n  - {persons}\n---\n\n# {title}\n\n> [!summary]\n> {body}\n")


CARDS = {
    **VAULT, "ontology.toml": ONTOLOGY,
    EASEL: _note("note-5e5e5e5e5e", "A new easel", "2026-10-14", "marge", "For the porch."),
    TICKETS: _note("note-6f6f6f6f6f", "Tickets for the park", "2026-10-15", "bart",
                   "Four tickets, one day."),
}


class Wiki:
    """The brain, the compile with the model, and the record of what it asked."""

    def __init__(self, root: Path, model: Model, monkeypatch, capsys):
        self.brain, self.vault = root / "brain", root / "vault"
        for base in (self.brain, self.vault):
            for path, text in CARDS.items():
                (base / path).parent.mkdir(parents=True, exist_ok=True)
                (base / path).write_text(text, encoding="utf-8")
        self.calls_log = root / "wiki-calls.jsonl"
        for name, value in (("BRAIN_REPO_DIR", self.brain), ("MEMORY_VAULT_DIR", self.vault),
                            ("SHARED_BUCKET", "family"), ("LANGUAGE", "en"),
                            ("WIKI_CALLS_LOG", self.calls_log)):
            monkeypatch.setenv(name, str(value))
        monkeypatch.setattr(models, "_DEFAULT_MODEL", "stand-in")
        self.model, self._monkeypatch, self._capsys = model, monkeypatch, capsys
        self.llm = LLM(AsyncOpenAI(base_url=model.url, api_key="none", max_retries=0),
                       namespace="memory-wiki")

    def compile(self, on: date, *flags: str, rc: int = 0) -> str:
        """`wiki` on day `on`, as the nightly sweep runs it; what it printed.
        It must exit with `rc`."""
        self._monkeypatch.setenv("STACK_TODAY", on.isoformat())
        self._capsys.readouterr()
        assert asyncio.run(wiki.run(self.llm, list(flags))) == rc
        return self._capsys.readouterr().err

    def asked(self) -> list[str]:
        """The pages the model was asked for since the last call, sorted."""
        if not self.calls_log.exists():
            return []
        calls = [json.loads(ln)["page"] for ln in self.calls_log.read_text().splitlines()]
        self.calls_log.unlink()
        return sorted(calls)

    def move(self, path: str, to: str) -> None:
        """Card `path` moved to `to`, its text the same, as the mirror moves it."""
        (self.brain / to).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(self.brain / path, self.brain / to)

    def page(self, path: str) -> str:
        return (self.brain / path).read_text(encoding="utf-8")


@pytest.fixture
def model(httpserver) -> Model:
    model = Model(httpserver)
    model.answers(ANSWER)
    return model


@pytest.fixture
def the_wiki(tmp_path, model, monkeypatch, capsys) -> Wiki:
    """The Simpsons' wiki, compiled with the model on Monday."""
    the_wiki = Wiki(tmp_path, model, monkeypatch, capsys)
    the_wiki.compile(MONDAY)
    assert the_wiki.asked() == EVERY_PAGE
    return the_wiki


class TestTheNextNightWithNothingFiled:
    def test_only_the_home_page_asks_the_model(self, the_wiki):
        """Given a sweep on Monday, when the sweep runs on Tuesday with nothing
        filed, then the model is asked only for the home page, whose prompt
        carries the day, and the log names each page it was not asked for."""
        before = views(the_wiki.brain)

        printed = the_wiki.compile(TUESDAY)

        assert the_wiki.asked() == ["index.md"]
        for page in (MARGE, BART, TOPIC):
            assert f"{page}: unchanged since the model wrote it, not asked again" in printed
        assert {p: t for p, t in views(the_wiki.brain).items() if p != "index.md"} \
            == {p: t for p, t in before.items() if p != "index.md"}

    def test_the_same_day_asks_nothing(self, the_wiki):
        before = views(the_wiki.brain)

        the_wiki.compile(MONDAY)

        assert the_wiki.asked() == []
        assert views(the_wiki.brain) == before


class TestACardThatChanged:
    def test_asks_the_model_again_for_the_pages_that_read_it(self, the_wiki):
        """Given a sweep, when a card the pages read is retitled, then the
        model is asked again for each of them, and for no other page."""
        card = the_wiki.brain / ART
        card.write_text(card.read_text().replace("title: Art supplies for next time",
                                                 "title: Art supplies for the lake"))

        the_wiki.compile(MONDAY)

        # The topic page reads it through its tag.
        assert the_wiki.asked() == sorted([TOPIC, "index.md", MARGE])

    def test_a_card_moved_in_place_keeps_its_citation(self, the_wiki):
        """Given a sweep, when a card moves to another path with its text
        the same, then the model is asked again for the pages that cite it,
        and they cite it at its new path.

        The path is in no prompt; only the sources an answer cites name it.
        """
        moved = "marge/notes/2026/10/easel-for-the-porch.md"
        the_wiki.move(EASEL, moved)

        the_wiki.compile(MONDAY)

        assert MARGE in the_wiki.asked()
        assert f"/{moved}" in the_wiki.page(MARGE)


class TestAnAnswerThatLostACitation:
    def test_is_written_again_when_the_card_is_back(self, the_wiki):
        """Given a sweep, when a card Marge's page cites is gone during a
        compile without the model and back for the next sweep, then the
        model is asked for her page again and it cites the card again.

        The compile without the model drops the citation of a card that is
        gone; the prompt that comes back is the one the old answer had.
        """
        easel = (the_wiki.brain / EASEL).read_text()
        (the_wiki.brain / EASEL).unlink()
        the_wiki.compile(MONDAY, "--fast")
        assert f"/{EASEL}" not in the_wiki.page(MARGE)
        (the_wiki.brain / EASEL).write_text(easel)

        the_wiki.compile(MONDAY)

        assert MARGE in the_wiki.asked()
        assert f"/{EASEL}" in the_wiki.page(MARGE)


class TestASourceAddedAndRemovedWithoutTheModel:
    def test_asks_the_model(self, the_wiki):
        """Given a sweep, when a card for Marge is filed and compiled without
        the model, and gone again before the next sweep, then the model is
        asked for her page: its kept answer cited a card that is gone.

        Her prompt is the one of the first sweep again, so the request
        matches; the answer it keeps does not.
        """
        brush = "marge/notes/2026/10/brush.md"
        (the_wiki.brain / brush).write_text(
            _note("note-7a7a7a7a7a", "A new brush", "2026-10-16", "marge", "Sable, size 6."))
        the_wiki.compile(MONDAY, "--fast")
        (the_wiki.brain / brush).unlink()

        printed = the_wiki.compile(MONDAY)

        assert MARGE in the_wiki.asked()
        assert f"{MARGE}: unchanged" not in printed


class TestAnAnswerThatIsNotJSON:
    def test_fails_the_page_and_says_so(self, the_wiki, model):
        """Given a sweep, when a card changes and the model answers Marge's
        page with prose instead of JSON, then the compile of her page fails,
        says the answer was unusable, and leaves the page as it was."""
        before = the_wiki.page(MARGE)
        card = the_wiki.brain / ART
        card.write_text(card.read_text().replace("title: Art supplies for next time",
                                                 "title: Art supplies for the lake"))
        model.answering(lambda messages: "Here is Marge's page: she paints.")

        printed = the_wiki.compile(MONDAY, "--member", "marge", rc=1)

        assert "the model's answer is unusable" in printed
        assert the_wiki.page(MARGE) == before


class TestAPersonPageInANewLayout:
    def test_is_written_once_by_the_model(self, the_wiki, monkeypatch):
        """Given a sweep, when a release bumps the person page's layout
        stamp, then the model is asked for each person page once."""
        monkeypatch.setattr(wiki, "_PERSON_PAGE_STAMP", "<!-- person page: 99 -->")

        the_wiki.compile(MONDAY)
        assert the_wiki.asked() == [BART, MARGE]
        the_wiki.compile(MONDAY)
        assert the_wiki.asked() == []


class TestAKeptAnswerThatNoLongerReads:
    def test_asks_the_model_instead(self, the_wiki, model, monkeypatch):
        """Given a sweep, when a release reads a person's answer more
        strictly and the kept answer no longer passes, then the model is
        asked for the page and its new answer is written."""
        strict = person_page.read_answer

        def stricter(raw, **kwargs):
            if json.loads(raw).get("tagline") == ANSWER["tagline"]:
                raise person_page.AnswerError("the tagline is too plain now")
            return strict(raw, **kwargs)

        monkeypatch.setattr(person_page, "read_answer", stricter)
        model.answers({**ANSWER, "tagline": "Paints the family."})

        the_wiki.compile(MONDAY)

        assert the_wiki.asked() == [BART, MARGE]
        assert "Paints the family." in the_wiki.page(MARGE)


class TestAnotherModel:
    def test_asks_again_for_every_page(self, the_wiki, monkeypatch):
        monkeypatch.setattr(models, "_DEFAULT_MODEL", "another")

        the_wiki.compile(MONDAY)

        assert the_wiki.asked() == EVERY_PAGE


class TestAPageKeptBeforeThisChange:
    def test_asks_once_and_then_no_more(self, the_wiki):
        """Given a page whose kept answer records no request, as every page
        compiled before has, when the sweep runs twice, then the model is
        asked for it on the first and not on the second."""
        _forget_request(the_wiki.brain / MARGE)

        the_wiki.compile(MONDAY)
        assert the_wiki.asked() == [MARGE]
        the_wiki.compile(MONDAY)
        assert the_wiki.asked() == []


def _forget_request(page: Path) -> None:
    """`page` with its kept answer as views kept it before they recorded the request."""
    text = page.read_text(encoding="utf-8")
    line = re.search(r"<!-- answer: ([A-Za-z0-9+/=]*) -->", text)
    assert line, f"{page} keeps an answer"
    kept = json.loads(base64.b64decode(line[1]))
    old = base64.b64encode(json.dumps(
        {"answer": kept["answer"], "sources": kept["sources"]}).encode()).decode()
    page.write_text(text.replace(line[1], old), encoding="utf-8")


class TestNoBrain:
    def test_a_model_is_asked_as_if_nothing_was_kept(self, monkeypatch):
        """Given no `BRAIN_REPO_DIR`, when a view's answer is asked for, then
        the model is asked: there is no kept answer to compare with."""
        monkeypatch.delenv("BRAIN_REPO_DIR", raising=False)

        class StandIn:
            def __init__(self) -> None:
                self.prompts: list[str] = []

            def request_key(self, role):
                return ("stand-in",)

            async def complete(self, role, prompt, **kwargs):
                self.prompts.append(prompt)
                return json.dumps(ANSWER)

        model = StandIn()
        answer, _ = asyncio.run(wiki._ask(model, MARGE, "the prompt", [], json.loads))

        assert answer == ANSWER and model.prompts == ["the prompt"]
