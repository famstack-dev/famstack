"""The AI's first start offers the default chat model only on the engine the stack runs.

The first `stack up ai` looks for the default chat model on the AI
server. On the oMLX the stack installed it downloads it without a
question, through the downloader the preparation uses
(`model_download.py`). An AI server the admin runs, the oMLX app or one on another
machine, is the admin's to fill (route 2): famstack downloads and loads
nothing there, asks nothing, and warns with the models the server has.

`ensure_model` runs as the first start runs it, against a real HTTP
server standing in for oMLX's admin API (`omlx_stand_in.py`), with the
admin's answers typed into a stand-in terminal.
"""

from __future__ import annotations

import sys


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


def _no_question_asked() -> bool:
    """True when the admin's typed answer is still unread: nothing asked."""
    return sys.stdin.tell() == 0


# ── The engine the stack runs ────────────────────────────────────────────

def test_the_stacks_own_omlx_downloads_the_model_without_a_question_and_loads_it(
        tmp_path, httpserver, terminal):
    """Given the engine the stack runs lacks the model, then it is
    downloaded and loaded without a question: choosing that engine was the
    consent to download its model."""
    omlx = OMLX(httpserver)
    terminal.types("n")

    result = ensure_model(_instance(tmp_path, httpserver, provider="managed"), MODEL)

    assert _no_question_asked()
    assert omlx.requested == [MODEL]
    assert omlx.loaded == ["Qwen3.5-9B-MLX-8bit"]
    assert result == {"loaded": True, "model": "Qwen3.5-9B-MLX-8bit"}


def test_a_later_start_asks_nothing_when_the_model_is_there(tmp_path, httpserver, terminal):
    httpserver.expect_request("/v1/models").respond_with_json({"data": [{"id": "Qwen3.5-9B-MLX-8bit"}]})
    terminal.types("y")

    result = ensure_model(_instance(tmp_path, httpserver), MODEL)

    assert result["loaded"] and _no_question_asked()


# ── An oMLX the admin runs ───────────────────────────────────────────────

def test_the_omlx_app_gets_nothing_and_a_warning(tmp_path, httpserver, terminal):
    omlx = OMLX(httpserver)
    terminal.types("y")

    result = ensure_model(_instance(tmp_path, httpserver), MODEL)

    assert "warning" in result
    assert _no_question_asked()
    assert omlx.requested == [] and omlx.loaded == []


def test_one_elsewhere_gets_nothing_either(tmp_path, httpserver, terminal):
    omlx = OMLX(httpserver)
    terminal.types("y")

    result = ensure_model(_instance(tmp_path, httpserver), MODEL)

    assert "warning" in result and _no_question_asked()
    assert omlx.requested == [] and omlx.loaded == []


# ── A server that is not an oMLX ─────────────────────────────────────────

def test_a_server_without_omlxs_admin_api_keeps_the_warning(tmp_path, httpserver, terminal):
    httpserver.expect_request("/v1/models").respond_with_json({"data": [{"id": "gpt-4.1-mini"}]})
    terminal.types("y")

    result = ensure_model(_instance(tmp_path, httpserver), MODEL)

    assert "warning" in result and "gpt-4.1-mini" in result["warning"]
    assert _no_question_asked()
    assert [r.path for r, _ in httpserver.log if r.method == "POST"] == []


def test_the_setup_says_again_when_the_server_lacks_the_default_model(
        tmp_path, httpserver, capsys):
    """Given `[ai]` names an oMLX the admin runs without `[ai] default`,
    when `stack setup ai` runs the setup hook again, then it warns that the
    model is missing there, and downloads nothing."""
    import types

    from stacklets.ai.hooks import on_install_success

    omlx = OMLX(httpserver)
    instance = _instance(tmp_path, httpserver)
    ai = {"provider": "external", "openai_url": httpserver.url_for("/v1"),
          "openai_key": KEY, "default": MODEL, "speech": False}
    ctx = types.SimpleNamespace(
        stack=types.SimpleNamespace(root=instance, config={"ai": ai}),
        cfg=lambda key, default=None: ai.get(key, default))

    on_install_success.run(ctx)

    assert "Qwen3.5-9B-MLX-8bit" in capsys.readouterr().out
    assert omlx.requested == []
