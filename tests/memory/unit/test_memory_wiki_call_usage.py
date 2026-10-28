"""What a wiki model call costs, on the compile's log.

Every page the model writes leaves one line on the compile's log: the
page, the prompt's size as estimated before the call, the token counts the
server reports (or that it reported none), and the seconds the call took.
Later work on prompts and budgets measures against that line.

The compile runs as the stack runs it: `wiki --home`, memory's entry
point in the bot runner, started in a process of its own as `docker exec`
starts it, over a brain that holds the Simpsons' cards as the curator's
mirror leaves them. What stands in is the model, an HTTP server answering
the chat completions API (`Model`), with or without a `usage` block.

A test takes about two seconds: the compile starts an interpreter of its
own.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from stack.bot_runner import BOT_RUNNER_CONTAINER
from tests.memory.unit.instance import Model, running_memory
from tests.memory.unit.simpsons_cards import ONTOLOGY, VAULT

THE_MEMORY_BOT = "/stacklets/memory/bot/cli_entrypoint.py"

# A home page answer the page accepts: the model's turn is what is logged,
# so its content only has to be usable.
HOME = {"family": "The Simpsons", "welcome": "A loud, loving family. [1]",
        "order": [], "people": {}}


@pytest.fixture
def model(httpserver) -> Model:
    model = Model(httpserver)
    model.answers(HOME)
    return model


@pytest.fixture
def memory(tmp_path, model):
    """The Simpsons' memory, with the brain the wiki compiles into holding
    their cards, as the curator's mirror leaves it."""
    with running_memory(tmp_path, {**VAULT, "ontology.toml": ONTOLOGY},
                        seeded_by="memory-bot", seeded_as="cards: the Simpsons",
                        model=model.url) as running:
        shutil.copytree(running.vault, tmp_path / "memory" / "brain",
                        ignore=shutil.ignore_patterns(".git"))
        yield running


def compile_home() -> str:
    """`wiki --home` in the bot runner, as the curator runs it; its log."""
    done = subprocess.run(
        ["docker", "exec", BOT_RUNNER_CONTAINER, "python", THE_MEMORY_BOT, "wiki", "--home"],
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout + done.stderr


def cost_line(log: str) -> str:
    """The one line the home page's model call left."""
    lines = [ln for ln in log.splitlines() if "[llm] index.md:" in ln]
    assert len(lines) == 1, log
    return lines[0]


class TestTheCostOfAPage:
    def test_the_line_carries_the_servers_counts(self, memory, model):
        model.usage = {"prompt_tokens": 4321, "completion_tokens": 210,
                       "total_tokens": 4531, "prompt_tokens_details": {"cached_tokens": 4000}}

        line = cost_line(compile_home())

        assert re.search(r"~\d+ prompt tokens estimated", line)
        assert "4321 prompt (4000 cached), 210 completion" in line
        assert re.search(r"\d+\.\d+s$", line)

    def test_a_server_without_usage_says_so_and_the_page_is_written(self, memory, model):
        line = cost_line(compile_home())

        assert re.search(r"~\d+ prompt tokens estimated", line)
        assert "the server sent no usage" in line
        assert "A loud, loving family" in (memory.root / "memory" / "brain" / "index.md").read_text()
