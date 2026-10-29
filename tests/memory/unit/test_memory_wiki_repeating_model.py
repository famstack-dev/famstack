"""A wiki page whose model answer repeats itself is asked once more.

A model can fall into a loop while it writes a page: one sentence that
runs on until the answer's token cap, after which the cut-off answer does
not parse and the page is never written. The compile streams each page's
call, stops it as soon as the answer repeats itself, and asks once more
at a higher temperature, which moves the model off the looping path.

The compile runs as the stack runs it: `wiki --home`, memory's
entry point in the bot runner, started in a process of its own as
`docker exec` starts it, over a brain that holds the Simpsons' cards as
the curator's mirror leaves them. What stands in is the model, an HTTP
server that streams its answers (`Model`). The record the tests read is
the compile's `WIKI_CALLS_LOG`, one line per model call. The home page
stands for every page: each asks the model through the same call.

A case takes about 1.5 seconds, most of it the instance.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from stack.bot_runner import BOT_RUNNER_CONTAINER
from tests.memory.unit.instance import running_memory
from tests.model import Model
from tests.memory.unit.simpsons_cards import ONTOLOGY, VAULT

THE_MEMORY_BOT = "/stacklets/memory/bot/cli_entrypoint.py"

# The answer's token cap (`_ANSWER_TOKENS`). A looping answer the stand-in
# streams is this long, one word per chunk, so a call that is not stopped
# reads all of it.
CAP = 8192

# The loop the model fell into on a person's page (famstack#374), repeated
# to the cap and opened like a page answer.
LOOP = ('{"family": "The Simpsons", "welcome": "Homer works at the plant and '
        + " ".join(["is a Simpson and is Homer and is the best dad in the world"] * (CAP // 13)))
GOOD = {"family": "The Simpsons", "welcome": "A loud, loving family. [1]",
        "order": [], "people": {}}
WRITTEN = "A loud, loving family"


@pytest.fixture
def model(httpserver) -> Model:
    return Model(httpserver)


@pytest.fixture
def memory(tmp_path, model):
    """The Simpsons' memory, with the brain the wiki compiles into holding
    their cards, as the curator's mirror leaves it."""
    with running_memory(tmp_path, {**VAULT, "ontology.toml": ONTOLOGY},
                        seeded_by="memory-bot", seeded_as="cards: the Simpsons",
                        model=model.url) as running:
        shutil.copytree(running.vault, running.brain, ignore=shutil.ignore_patterns(".git"))
        yield running


def compile_home() -> subprocess.CompletedProcess:
    """`wiki --home` in the bot runner, as the curator runs it."""
    return subprocess.run(
        ["docker", "exec", BOT_RUNNER_CONTAINER, "python", THE_MEMORY_BOT, "wiki", "--home"],
        capture_output=True, text=True, timeout=60)


class TestAnAnswerThatRepeatsItself:
    def test_the_page_is_written_from_the_second_call(self, memory, model):
        """Given a model that loops on its first answer and answers well on
        the second, when the compile writes the home page, then the first call
        stops long before the cap, the second is asked at a higher
        temperature, and the page is written from its answer.
        """
        model.answers(LOOP, GOOD)

        done = compile_home()

        assert done.returncode == 0, done.stderr
        assert WRITTEN in (memory.brain / "index.md").read_text(encoding="utf-8")
        first, second = memory.calls()
        assert first["finish_reason"] == "repeating" and first["ok"] is False
        assert first["completion_tokens"] < CAP // 4
        assert second["ok"] is True and second["finish_reason"] == "stop"
        assert second["temperature"] > first["temperature"] == 0
        assert [s["temperature"] for s in model.settings] == [first["temperature"],
                                                              second["temperature"]]
        assert all(s["stream"] for s in model.settings)

    def test_a_retry_that_repeats_too_leaves_the_page_as_it_was(self, memory, model):
        """Given a model that loops on both calls, when the compile writes
        the home page, then the page stays as it was, the log says both calls
        repeated, and the record has a line for each.
        """
        before = "---\ntitle: The Simpsons\ngenerated: true\n---\n\nThe family, as of last night.\n"
        page = memory.brain / "index.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(before, encoding="utf-8")
        model.answers(LOOP)

        done = compile_home()

        assert done.returncode == 1
        assert page.read_text(encoding="utf-8") == before
        assert "both calls repeated" in done.stderr
        assert [c["finish_reason"] for c in memory.calls()] == ["repeating", "repeating"]


class TestAnAnswerThatFinishes:
    def test_one_call_at_temperature_zero_with_the_same_cap(self, memory, model):
        """Given a model that answers in full, when the compile writes
        the home page, then it asks once, at temperature 0, capped as before.
        """
        model.answers(GOOD)

        done = compile_home()

        assert done.returncode == 0, done.stderr
        [call] = memory.calls()
        assert call["ok"] is True
        assert (call["temperature"], call["max_tokens"]) == (0, CAP)
        assert WRITTEN in (memory.brain / "index.md").read_text(encoding="utf-8")
