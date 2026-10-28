"""What a wiki model call costs, in the compile's record.

Every model call the compile makes leaves one record in the file
`WIKI_CALLS_LOG` names: the page, the prompt's size as estimated before
the call, the token counts the server reports (None where it sent none),
the seconds, and whether it answered or the error. Later work on prompts
and budgets reads that record back, so the tests read its fields, never a
log line.

The compile runs as the stack runs it: `wiki --home`, memory's entry
point in the bot runner, started in a process of its own as `docker exec`
starts it, over a brain that holds the Simpsons' cards as the curator's
mirror leaves them. The bot runner's environment names the record's file
as core renders it, the file the curator names for its own compiles
(`curator.calls_log`). What stands
in is the model, an HTTP server answering the chat completions API
(`Model`), with or without a `usage` block, or refusing.

A case takes about 1.5 seconds (measured: 0.75 s for the compile, the
rest the instance): the compile starts an interpreter of its own, which
imports the model client.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from stack.bot_runner import BOT_RUNNER_CONTAINER
from tests.memory.unit.instance import Model, running_memory
from tests.memory.unit.simpsons_cards import ONTOLOGY, VAULT

THE_MEMORY_BOT = "/stacklets/memory/bot/cli_entrypoint.py"

# A home page answer the page accepts: the model's turn is what is
# recorded, so its content only has to be usable.
HOME = {"family": "The Simpsons", "welcome": "A loud, loving family. [1]",
        "order": [], "people": {}}
WELCOME = "A loud, loving family"


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
        shutil.copytree(running.vault, running.brain, ignore=shutil.ignore_patterns(".git"))
        yield running


def compile_home() -> subprocess.CompletedProcess:
    """`wiki --home` in the bot runner, as the curator runs it."""
    return subprocess.run(
        ["docker", "exec", BOT_RUNNER_CONTAINER, "python", THE_MEMORY_BOT, "wiki", "--home"],
        capture_output=True, text=True, timeout=60)


class TestTheCompileRecordsEachModelCall:
    def test_the_record_carries_the_servers_counts(self, memory, model):
        """Given a server that reports its token counts, when the compile
        writes the home page, then one record names the page with those
        counts, an estimate and the seconds, and the compile prints what it
        printed before.

        The server's counts are what every later prompt and budget change is
        measured against; a person updating the wiki must not see them.
        """
        model.usage = {"prompt_tokens": 4321, "completion_tokens": 210,
                       "total_tokens": 4531, "prompt_tokens_details": {"cached_tokens": 4000}}

        done = compile_home()

        assert done.returncode == 0, done.stderr
        [call] = memory.calls()
        assert (call["kind"], call["page"]) == ("view", "index.md")
        assert call["ok"] is True and call["error"] is None
        assert call["finish_reason"] == "stop" and call["max_tokens"] > 0
        assert (call["prompt_tokens"], call["cached_tokens"], call["completion_tokens"]) \
            == (4321, 4000, 210)
        assert call["estimated_tokens"] > 0
        assert call["seconds"] >= 0
        assert (done.stdout + done.stderr).splitlines() == ["published index.md"]

    @pytest.mark.parametrize("usage", [None, {}], ids=["no usage", "empty usage"])
    def test_a_server_without_counts_leaves_them_empty(self, memory, model, usage):
        """Given a server that sends no `usage`, or one without its fields,
        when the compile writes the home page, then the record has no
        counts, keeps the estimate, and the page is written.

        Not every server counts tokens; the compile must go on, and the
        record must not invent a zero.
        """
        model.usage = usage

        done = compile_home()

        assert done.returncode == 0, done.stderr
        [call] = memory.calls()
        assert call["ok"] is True
        assert (call["prompt_tokens"], call["cached_tokens"], call["completion_tokens"]) \
            == (None, None, None)
        assert call["estimated_tokens"] > 0
        assert WELCOME in (memory.brain / "index.md").read_text(encoding="utf-8")

    def test_a_refused_call_is_recorded_with_its_error(self, memory, model):
        """Given a server that refuses the prompt (HTTP 400, as for a prompt
        over its context), when the compile asks for the home page, then the
        record names the page as failed, with the error, the estimate and
        the seconds, and no page is written.

        The calls that fail are the ones the budget work most needs to see.
        """
        model.refuses(400)

        done = compile_home()

        assert done.returncode == 1, done.stderr
        [call] = memory.calls()
        assert call["page"] == "index.md"
        assert call["ok"] is False and "400" in call["error"]
        assert (call["prompt_tokens"], call["completion_tokens"]) == (None, None)
        assert call["estimated_tokens"] > 0 and call["seconds"] >= 0
        assert not (memory.brain / "index.md").exists()

    def test_a_record_that_cannot_be_written_costs_no_page(self, memory, model):
        """Given a record file that cannot be written (a directory stands
        where it should be), when the compile writes the home page, then
        the page is written all the same.

        Accounting for a call must never cost the family its page.
        """
        memory.calls_log.mkdir(parents=True)

        done = compile_home()

        assert done.returncode == 0, done.stderr
        assert WELCOME in (memory.brain / "index.md").read_text(encoding="utf-8")
