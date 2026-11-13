"""`stack up ai` ends by asking the AI one short question, and keeps its answer.

A server can list its models, which the run's health check asks for, and
still fail every completion: an oMLX built for a newer macOS does. So the
AI's run asks one tiny question after its health check, says how it went
as a step of the run, and keeps the answer in the instance for the setup
page (`stack ai recommended app`). A failure is a failed step with the
server's error, worded by its kind; the run itself still ends, so a page
that follows it never waits for ever.

The hook runs as `stack up ai` runs it, against the real ai stacklet and
a throwaway instance, with the oMLX stand-in on localhost. A `docker` and
a `brew` on PATH record any command, so nothing touches this Mac.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from stack import Stack
from stack.hooks import HookResolver, build_hook_ctx
from stack.output import CollectorOutput, EventOutput
from tests.unit.stacklets.omlx_stand_in import BROKEN, KEY, OMLX, hanging

REPO_ROOT = Path(__file__).resolve().parents[3]
AI = REPO_ROOT / "stacklets" / "ai"
MODEL = "mlx-community/Qwen3.5-9B-MLX-4bit"


@pytest.fixture(autouse=True)
def host_commands(tmp_path, monkeypatch):
    """A `docker` and a `brew` that do nothing, first on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("docker", "brew"):
        (bin_dir / name).write_text("#!/bin/sh\n")
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    # Speech stays out of these runs: the house voice's download is not
    # what they look at.
    monkeypatch.setenv("STACK_AI_NO_VOICE", "1")


def _instance(tmp_path, server) -> Stack:
    """An instance whose AI is the oMLX stand-in, with its key and model."""
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\n\n'
        f'[ai]\nprovider = "managed"\nopenai_url = "{server.url_for("/v1")}"\n'
        f'openai_key = "{KEY}"\ndefault = "{MODEL}"\nspeech = false\n')
    (tmp_path / ".stack").mkdir()
    return Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path, output=CollectorOutput())


def _run(stack: Stack) -> bool:
    """`on_start_ready` of `stack up ai`, the way the CLI runs it."""
    ctx = build_hook_ctx("ai", env=stack.env("ai"), step_fn=stack.output.step, stack=stack)
    return HookResolver(AI).run("on_start_ready", ctx)


def _kept(stack: Stack) -> dict | None:
    """What the setup page reads of the AI's answer."""
    return stack.run_cli_command("ai", "recommended", ["app"])["answer"]


def test_the_run_asks_the_ai_and_keeps_its_answer(tmp_path, httpserver):
    omlx = OMLX(httpserver, wants_key=True)
    stack = _instance(tmp_path, httpserver)

    assert _run(stack)

    assert omlx.completed == [MODEL]
    assert _kept(stack)["ok"] is True
    assert stack.output.warnings == []


def test_a_server_that_cannot_compute_is_a_failed_step_with_its_error(tmp_path, httpserver):
    """The run's question is a step a screen follows: it fails, with the
    server's own error, and the answer is kept; the run's other work is
    not undone."""
    OMLX(httpserver, wants_key=True, computes=False)
    stack = _instance(tmp_path, httpserver)
    events = io.StringIO()
    stack.output = EventOutput(CollectorOutput(), events)

    assert _run(stack)

    failed = [json.loads(line) for line in events.getvalue().splitlines()
              if json.loads(line)["type"] == "step.failed"]
    assert len(failed) == 1 and BROKEN in json.dumps(failed[0]["data"]), events.getvalue()
    kept = _kept(stack)
    assert kept["ok"] is False and kept["reason"] == "server"


def test_a_server_that_hangs_ends_the_run_with_a_timeout(tmp_path, monkeypatch):
    """A first model load that takes longer than the run waits ends the
    run's question with a timeout, which is kept: a page then says so
    instead of waiting."""
    monkeypatch.setattr("stack.ai.probe.ANSWER_SECONDS", 0.2)
    with hanging() as server:
        stack = _instance(tmp_path, server)

        assert _run(stack)

    assert _kept(stack)["reason"] == "timeout"
