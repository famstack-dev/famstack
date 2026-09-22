"""Starting the agent needs an AI server, not the local AI stacklet.

The agent talks to whatever `[ai] openai_url` points at: the local
engine, a machine elsewhere on the network, or a hosted provider. It
used to refuse to start unless the local AI stacklet was set up, which
locked out every household running its models on another machine.

What it does need is an address. Without one the agent could only
start and then fail every message, so `stack up agent` stops and says
how to configure one. An address that does not answer right now is a
warning, not a stop: the server may be booting, and a machine that is
switched off at night should not keep the agent from coming back up.
"""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AGENT = _REPO_ROOT / "stacklets" / "agent"

_spec = importlib.util.spec_from_file_location(
    "agent_hooks_on_start", _AGENT / "hooks" / "on_start.py")
on_start = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(on_start)


class FakeCtx:
    """The agent's rendered env, as the framework hands it to the hook."""

    def __init__(self, url, model="llama3.1:8b"):
        self.env = {"AGENT_OPENAI_URL": url, "AGENT_OPENAI_KEY": "k",
                    "AGENT_MODEL": model}


def test_the_local_ai_stacklet_is_not_a_requirement():
    manifest = tomllib.loads((_AGENT / "stacklet.toml").read_text())
    assert "ai" not in manifest["requires"]
    assert "messages" in manifest["requires"]


def test_no_ai_server_stops_the_start_and_names_both_ways_to_get_one():
    with pytest.raises(RuntimeError) as e:
        on_start.run(FakeCtx(url=""))
    assert "stack up ai" in str(e.value)
    assert "stack ai connect" in str(e.value)


def test_a_server_that_answers_starts_quietly(httpserver, capsys):
    httpserver.expect_request("/v1/models").respond_with_json(
        {"data": [{"id": "llama3.1:8b"}]})

    on_start.run(FakeCtx(url=httpserver.url_for("/v1")))

    assert "⚠" not in capsys.readouterr().out


def test_a_server_that_does_not_answer_warns_and_starts_anyway(capsys):
    on_start.run(FakeCtx(url="http://127.0.0.1:9/v1"))

    assert "127.0.0.1:9" in capsys.readouterr().out


def test_a_model_the_server_does_not_have_is_named(httpserver, capsys):
    """The installer picks an MLX model id. A server running something
    else answers every request with an error the family never sees."""
    httpserver.expect_request("/v1/models").respond_with_json(
        {"data": [{"id": "llama3.1:8b"}]})

    on_start.run(FakeCtx(url=httpserver.url_for("/v1"),
                         model="mlx-community/Qwen3.5-9B-MLX-8bit"))

    assert "Qwen3.5-9B-MLX-8bit" in capsys.readouterr().out
