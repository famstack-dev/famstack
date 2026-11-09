"""The AI's first start offers the default chat model on any oMLX.

The first `stack up ai` looks for the default chat model on the AI
server and offers to download it when the server is an oMLX: the one the
stack installed, and as well one the admin runs, such as the oMLX app the
setup offered ("Use it?"). On this Mac the offer is the stack's own,
default yes. On another machine it says where the download goes, default
no. A server that is not an oMLX keeps the warning: its admin loads
models there.

`ensure_model` runs as the first start runs it, against a real HTTP
server standing in for oMLX's admin API (`omlx_stand_in.py`), with the
admin's answers typed into a stand-in terminal.
"""

from __future__ import annotations

import pytest

from stacklets.ai.backend import ensure_model
from tests.unit.stacklets.omlx_stand_in import KEY, OMLX

MODEL = "mlx-community/Qwen3.5-9B-MLX-8bit"


def _instance(tmp_path, server, provider: str = "external"):
    """An instance whose `[ai]` names `server`, as `stack ai switch` writes it."""
    url = server.url_for("/v1")
    (tmp_path / "stack.toml").write_text(
        f'[ai]\nprovider = "{provider}"\nopenai_url = "{url}"\nopenai_key = "{KEY}"\n'
        f'default = "{MODEL}"\n')
    return tmp_path


@pytest.fixture
def answers(monkeypatch):
    """The admin answers each question with `answer`; returns the
    questions asked."""
    def answering(answer: str) -> list[str]:
        prompts: list[str] = []
        monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or answer)
        return prompts
    return answering


@pytest.fixture
def elsewhere(monkeypatch):
    """The stand-in plays a server on another machine. The unit lane
    reaches only this Mac, so the stand-in's address is said not to be
    this Mac's. Patched in the framework's module, which every loader of
    the AI stacklet shares, not in a copy of the stacklet's own."""
    monkeypatch.setattr("stack.ai.probe.on_this_mac", lambda _url: False)


# ── An oMLX on this Mac ──────────────────────────────────────────────────

def test_the_omlx_app_gets_the_offer_with_the_size_and_the_model_is_loaded(
        tmp_path, httpserver, answers, capsys):
    omlx = OMLX(httpserver)
    prompts = answers("")

    result = ensure_model(_instance(tmp_path, httpserver), MODEL)

    [question] = prompts
    assert "Download from HuggingFace?" in question and "Y/n" in question
    assert "5.8 GB" in capsys.readouterr().out
    assert omlx.requested == [MODEL]
    assert omlx.loaded == ["Qwen3.5-9B-MLX-8bit"]
    assert result == {"loaded": True, "model": "Qwen3.5-9B-MLX-8bit"}


def test_the_stacks_own_omlx_gets_the_same_offer(tmp_path, httpserver, answers):
    omlx = OMLX(httpserver)
    answers("")

    ensure_model(_instance(tmp_path, httpserver, provider="managed"), MODEL)

    assert omlx.requested == [MODEL]


def test_a_later_start_asks_nothing_when_the_model_is_there(tmp_path, httpserver, answers):
    httpserver.expect_request("/v1/models").respond_with_json({"data": [{"id": "Qwen3.5-9B-MLX-8bit"}]})
    prompts = answers("y")

    result = ensure_model(_instance(tmp_path, httpserver), MODEL)

    assert result["loaded"] and prompts == []


# ── An oMLX on another machine ───────────────────────────────────────────

def test_one_elsewhere_says_where_and_no_is_the_default(tmp_path, httpserver, answers, elsewhere):
    omlx = OMLX(httpserver)
    prompts = answers("")

    result = ensure_model(_instance(tmp_path, httpserver), MODEL)

    [question] = prompts
    assert "This downloads 5.8 GB onto the AI server at 127.0.0.1. Download it there?" in question
    assert "y/N" in question
    assert result == {"skipped": True, "model": MODEL}
    assert omlx.requested == [] and omlx.loaded == []


def test_one_elsewhere_gets_it_after_a_yes(tmp_path, httpserver, answers, elsewhere):
    omlx = OMLX(httpserver)
    answers("y")

    ensure_model(_instance(tmp_path, httpserver), MODEL)

    assert omlx.requested == [MODEL]
    assert omlx.loaded == ["Qwen3.5-9B-MLX-8bit"]


# ── A server that is not an oMLX ─────────────────────────────────────────

def test_a_server_without_omlxs_admin_api_keeps_the_warning(tmp_path, httpserver, answers):
    httpserver.expect_request("/v1/models").respond_with_json({"data": [{"id": "gpt-4.1-mini"}]})
    prompts = answers("y")

    result = ensure_model(_instance(tmp_path, httpserver), MODEL)

    assert "warning" in result and "gpt-4.1-mini" in result["warning"]
    assert prompts == []
    assert [r.path for r, _ in httpserver.log if r.method == "POST"] == []
