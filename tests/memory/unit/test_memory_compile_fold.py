"""The home page folds what is filed into its base, within the call budget.

The home and person pages keep a base: the model's answer from the last
night, the cards it cites and the cards it had read (`stacklets/memory/
bot/fold.py`). These tests drive one fabricated family of 50 filings
through the curator's loop, as the prompt measurement does
(`compile_prompts.py`),
and read what a client of the compile can see: the model's requests, the
compile's record of its calls (`WIKI_CALLS_LOG`) and the home page in the
wiki's working copy, whose kept answer carries its base.

The model's window is set small, so the budget is about 2,000 tokens and
the 50 filings take more than one call:

    seed        no base: the night builds the home page in chunks
    document    a school letter filed by day: the base stays as it was
    night       the night folds the letter in: the answer is the new base
    correction  the letter corrected: the item only it backed is dropped
    burst       60 filings by day, more than one call holds; then the night
                folds the rest, chunk by chunk
    overflow    a window too small for the instructions: no call is made

The stand-in model keeps the base it is given and adds an item for each
school letter among the new sources, citing only that letter, so what
the base holds is the compiler's doing.

The scenarios run in that order on one memory, measured once (`run`). The
file takes about 40 seconds: the curator's first boot and three nights,
each step of its loop and each compile a process of its own.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pytest
from pytest_httpserver import HTTPServer

from stacklets.memory.bot import fold
from tests.memory.unit.compile_prompts import answer_for, filed, page_of, prompt_of
from tests.memory.unit.fabricated_family import TODAY, Filing, fabricated_family
from tests.memory.unit.instance import Model, running_memory

# A model that holds this many tokens leaves a call about 2,000 of them:
# the window less the answer cap (8,192) and a tenth of the window.
SMALL_WINDOW = 11_400
BUDGET = fold.budget(SMALL_WINDOW, 8_192)
# Too small for the home page's instructions alone.
TINY_WINDOW = 9_400

_ANSWER_LINE = re.compile(r"^<!-- answer: (\S+) -->$", re.M)
_SOURCE = re.compile(r"^\[(\d+)\] (?:\d{4}-\d{2}-\d{2} · )?(.+?)(?:, filed by .*)?$", re.M)


# ── The model ────────────────────────────────────────────────────────────

def home_answer(prompt: str) -> str:
    """The base in `prompt` kept as it is, with a "glance" item for each
    school letter among the sources filed since, citing only that letter."""
    answer = {"family": "The Simpsons", "welcome": "The Simpsons of Springfield."}
    if "THE HOME PAGE AS IT STANDS:\n" in prompt:
        answer = json.loads(prompt.split("THE HOME PAGE AS IT STANDS:\n", 1)[1].split("\n", 1)[0])
    since = prompt.split("What the family has filed", 1)[-1].split("\nToday is ", 1)[0] \
        if "What the family has filed" in prompt else ""
    letters = [{"label": title, "value": "a letter", "cite": [int(n)]}
               for n, title in _SOURCE.findall(since) if title.startswith("School letter")]
    answer["glance"] = answer.get("glance", []) + letters
    return json.dumps(answer)


def answer(messages: list[dict]) -> str:
    """The home page as `home_answer` writes it, every other page as the
    prompt measurement's stand-in does."""
    prompt = prompt_of(messages)
    return home_answer(prompt) if page_of(prompt) == "home" else answer_for(messages)


# ── The run ──────────────────────────────────────────────────────────────

@dataclass
class Step:
    """What one scenario left behind: the home page's prompts and call
    records, and its base afterwards."""

    prompts: list[str] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    base: fold.Base | None = None
    base_written: str = ""


@dataclass
class Run:
    steps: dict[str, Step]
    letter: str = ""          # the card id of the school letter filed by day
    burst: list[str] = field(default_factory=list)   # the card ids of the burst


def home_base(brain: Path) -> tuple[fold.Base | None, str]:
    """The base the home page keeps with its answer, and the base as written."""
    found = _ANSWER_LINE.search((brain / "index.md").read_text(encoding="utf-8"))
    written = json.loads(base64.b64decode(found[1])).get("base", "") if found else ""
    return fold.decode(written) if written else None, written


def card_id(filing: Filing) -> str:
    return re.search(r"^card_id: (\S+)$", filing.text, re.M)[1]


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> Run:
    root = tmp_path_factory.mktemp("compile-fold")
    family = fabricated_family(50)
    bundle = family.history(root / "50.bundle")
    # On 127.0.0.1, as the lane pins `httpserver`: `localhost` can be ::1.
    server = HTTPServer(host="127.0.0.1")
    server.start()
    model = Model(server)
    model.answering(answer)
    steps: dict[str, Step] = {}
    result = Run(steps)
    day = os.environ.get("STACK_TODAY")
    try:
        with running_memory(root / "instance", {}, history=bundle, model=model.url) as memory:

            def on(when) -> None:
                os.environ["STACK_TODAY"] = when.isoformat()
                memory.on_day(when.isoformat())

            def scenario(name: str, *acts) -> None:
                start, records = len(model.requests), len(memory.calls())
                for act in acts:
                    act()
                base, line = home_base(memory.brain)
                prompts = [prompt_of(m) for m in model.requests[start:]]
                steps[name] = Step(
                    [p for p in prompts if page_of(p) == "home"],
                    [c for c in memory.calls()[records:] if c.get("page") == "index.md"],
                    base, line)

            def push(*filings: Filing) -> None:
                filed(memory, list(filings))
                memory.curators_loop("turns")

            on(TODAY)
            memory.with_context_window(SMALL_WINDOW)
            letter = family.document(TODAY)
            result.letter = card_id(letter)
            scenario("seed", lambda: memory.curators_loop("boot"),
                     lambda: memory.curators_loop("night"))
            scenario("document", lambda: push(letter))
            scenario("night", lambda: memory.curators_loop("night"))
            scenario("correction", lambda: push(family.corrected(letter, TODAY)))
            tomorrow = TODAY + timedelta(days=1)
            on(tomorrow)
            burst = family.burst(60, tomorrow)
            result.burst = [card_id(f) for f in burst]
            scenario("burst", lambda: push(*burst))
            scenario("burst night", lambda: memory.curators_loop("night"))
            memory.with_context_window(TINY_WINDOW)
            scenario("overflow", lambda: push(family.document(tomorrow)))
    finally:
        server.clear()
        server.stop()
        if day is None:
            os.environ.pop("STACK_TODAY", None)
        else:
            os.environ["STACK_TODAY"] = day
    return result


# ── The base ─────────────────────────────────────────────────────────────

def test_a_page_without_a_base_is_built_in_chunks(run):
    """Given a home page with no base and more cards than one call holds,
    when the night compiles it, then it is built in several calls, each
    within the budget, the last leaving nothing for later, and the base
    has read every card."""
    seed = run.steps["seed"]
    assert len(seed.prompts) > 1
    assert all(fold_tokens(p) <= BUDGET for p in seed.prompts)
    assert [c["deferred"] for c in seed.calls][-1] == 0
    assert all(c["deferred"] > 0 for c in seed.calls[:-1])
    assert sum(c["cards"] for c in seed.calls) == len(seed.base.read)


def test_the_base_survives_a_daytime_compile(run):
    """Given the night's base, when a letter is filed by day, then the call
    reads the base and the letter, the page shows the letter, and the base
    is the night's, byte for byte."""
    seed, day = run.steps["seed"], run.steps["document"]
    assert len(day.prompts) == 1 and day.calls[0]["cards"] == 1
    assert seed.base.answer in day.prompts[0]
    assert day.base_written == seed.base_written
    assert run.letter not in day.base.read


def test_the_night_makes_the_answer_the_new_base(run):
    """Given a letter filed by day, when the night comes, then the base has
    read the letter and holds the item the model wrote for it."""
    night = run.steps["night"]
    assert night.base_written != run.steps["document"].base_written
    assert run.letter in night.base.read
    assert [c.card for c in night.base.cites] == [run.letter]


def test_a_correction_drops_what_only_the_corrected_card_said(run):
    """Given a base with an item citing only the letter, when the letter is
    corrected, then the prompt's base no longer has that item and the
    corrected letter is sent again with the cards filed since."""
    night, fixed = run.steps["night"], run.steps["correction"]
    stood = fixed.prompts[0].split("THE HOME PAGE AS IT STANDS:\n", 1)[1].split("\n", 1)[0]
    assert "School letter" in night.base.answer
    assert "School letter" not in stood
    assert fixed.calls[0]["cards"] == 1
    assert "Corrected:" in fixed.prompts[0].split("What the family has filed since", 1)[1]


def test_more_cards_than_a_call_holds_wait_for_the_night(run):
    """Given 60 filings in a day, more than one call holds, when the day's
    compile runs, then it carries what fits and leaves the rest, the base
    unchanged; the night then folds the rest chunk by chunk, each call
    within the budget, until the base has read every one."""
    day, night = run.steps["burst"], run.steps["burst night"]
    assert len(day.prompts) == 1 and day.calls[0]["deferred"] > 0
    assert day.base_written == run.steps["correction"].base_written
    assert len(night.prompts) > 1
    assert all(fold_tokens(p) <= BUDGET for p in day.prompts + night.prompts)
    assert night.calls[-1]["deferred"] == 0
    assert set(run.burst) <= set(night.base.read)


def test_a_base_over_the_budget_asks_nothing(run):
    """Given a window too small for the home page's instructions, when a
    letter is filed, then the home page is not asked of the model and
    keeps its base."""
    over = run.steps["overflow"]
    assert over.prompts == [] and over.calls == []
    assert over.base_written == run.steps["burst night"].base_written


def fold_tokens(prompt: str) -> int:
    """`prompt`'s size as the compile estimates it, 3 characters a token."""
    return round(len(prompt) / 3)
