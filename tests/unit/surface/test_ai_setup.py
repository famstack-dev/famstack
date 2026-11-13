"""The ai stacklet's setup question, before its first `stack up ai`.

On a fresh instance it asks whether to set up oMLX, the engine the stack
runs on this Mac; a no installs nothing and names `stack ai switch` for a
server elsewhere. Once `[ai]` names a server, managed or not, it asks
nothing and probes nothing: the engine is chosen with `stack ai switch`.

These tests run `./stack configure ai` and `./stack up ai` in this
process. Homebrew is stood in by an empty `brew` first on PATH, since a
yes to oMLX needs it.
"""

from __future__ import annotations

import tomllib

import pytest

import stack.docker

from .conftest import StackCLI, write_instance


@pytest.fixture
def brew(tmp_path, monkeypatch):
    """A Mac with Homebrew: an empty `brew` first on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "brew").write_text("#!/bin/sh\n")
    (bin_dir / "brew").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")


def _instance(tmp_path, monkeypatch, ai: str = "") -> StackCLI:
    """An instance with this `[ai]`. The home directory is the test's: a
    run that got as far as installing would write oMLX's settings and a
    Whisper service there."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return StackCLI(write_instance(tmp_path, extra=ai), monkeypatch)


@pytest.fixture
def no_omlx_from_homebrew(monkeypatch, as_stack_runs_it):
    """Homebrew has no oMLX, whatever the Mac that runs the tests has."""
    from stacklets.ai import homebrew_omlx
    monkeypatch.setattr(homebrew_omlx, "LIST_COMMAND", ("false",))


@pytest.fixture
def fresh(tmp_path, monkeypatch, as_stack_runs_it, brew, no_omlx_from_homebrew) -> StackCLI:
    """An instance with no `[ai]` yet, on a Mac with Homebrew."""
    return _instance(tmp_path, monkeypatch)


# ── On a fresh instance ──────────────────────────────────────────────────

def test_a_fresh_instance_asks_whether_to_set_up_omlx(fresh):
    form = fresh("configure", "ai", "--json").answer["form"]

    assert form["id"] == "ai.omlx"
    assert form["schema"]["properties"]["setup_omlx"]["default"] is True


def test_the_question_tells_whoever_runs_an_ai_server_how_to_use_it(fresh):
    """A yes would install a second oMLX next to the one that already runs,
    and both would hold models in memory."""
    text = fresh("configure", "ai", "--json").answer["form"]["text"]

    assert "Already running oMLX or another AI server? Answer no" in text
    assert "./stack ai switch <its address> --key <its API key>" in text


def test_yes_sets_up_the_engine_on_this_mac(fresh):
    assert fresh("configure", "ai", "--json", "--answer", "setup_omlx=yes").answer == {"ready": True}

    toml = fresh.toml()
    assert 'provider = "managed"' in toml
    assert 'openai_url = "http://localhost:42060/v1"' in toml


def test_no_cancels_and_names_the_command_for_a_server_elsewhere(fresh):
    ran = fresh("configure", "ai", "--answer", "setup_omlx=no")

    assert ran.code == 1
    assert "Cancelled" in ran.err
    assert "./stack ai switch <its address> --key <its API key>" in ran.err
    assert "provider" not in fresh.toml()


def test_yes_without_homebrew_says_how_to_install_it(tmp_path, monkeypatch, as_stack_runs_it,
                                                    no_omlx_from_homebrew):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    no_brew = _instance(tmp_path, monkeypatch)

    ran = no_brew("configure", "ai", "--answer", "setup_omlx=yes")

    assert ran.code == 1
    assert "Homebrew" in ran.err
    assert "provider" not in no_brew.toml()


KEY = "the-simpsons-ai-key"
MODEL = "mlx-community/Qwen3.5-9B-MLX-8bit"


def _models(httpserver, key: str = ""):
    """An OpenAI-compatible server, listing one model.
    With a key it answers only a request carrying that key."""
    listed = {"data": [{"id": MODEL}]}
    if key:
        httpserver.expect_request(
            "/v1/models", headers={"Authorization": f"Bearer {key}"}).respond_with_json(listed)
        httpserver.expect_request("/v1/models").respond_with_data("unauthorized", status=401)
    else:
        httpserver.expect_request("/v1/models").respond_with_json(listed)


# ── Speech stored off, as the install leaves it on a small Mac ───────────

@pytest.fixture
def speech_off(tmp_path, monkeypatch, as_stack_runs_it, brew, httpserver) -> StackCLI:
    """An instance whose `[ai]` names a server elsewhere, as `stack ai
    switch` writes it, and whose install stored `[ai] speech = false`.
    `brew` notes every call it gets: building Whisper starts with
    `brew list cmake`. Docker is asked which stacklets run, and answers
    none."""
    _models(httpserver, KEY)
    monkeypatch.setattr(stack.docker, "project_states", lambda: {})
    monkeypatch.delenv("STACK_AI_NO_VOICE", raising=False)
    calls = tmp_path / "brew-calls"
    (tmp_path / "bin" / "brew").write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    return _instance(tmp_path, monkeypatch,
                     f'[ai]\nprovider = "external"\nopenai_url = "{httpserver.url_for("/v1")}"\n'
                     f'openai_key = "{KEY}"\nspeech = false\n')


def test_with_speech_stored_off_stack_up_ai_installs_no_whisper(speech_off, tmp_path):
    """Given `[ai] speech = false`, the first start behaves as `--no-voice`:
    no question, no Whisper build, no speech server stored for voice
    messages."""
    speech_off("up", "ai")

    assert not (tmp_path / "brew-calls").exists()
    assert not (tmp_path / "data" / "ai" / "whisper.cpp").exists()
    ai = tomllib.loads(speech_off.toml())["ai"]
    assert ai["provider"] == "external"
    assert ai.get("whisper_url", "") == ""


def test_with_a_server_set_the_first_start_asks_nothing(speech_off):
    """Given `[ai]` names a server, the first `stack up ai` asks no route
    again: there is no form to answer."""
    assert speech_off("configure", "ai", "--json").answer == {"ready": True}


# ── Does the AI answer: `stack ai check` ─────────────────────────────────

def _checked(tmp_path, monkeypatch, httpserver, *, computes: bool) -> StackCLI:
    """An instance whose `[ai]` names the oMLX app, stood in on localhost
    with its key and its default model; with `computes` False the app
    lists its models and fails every completion."""
    from tests.unit.stacklets.omlx_stand_in import KEY as APP_KEY
    from tests.unit.stacklets.omlx_stand_in import OMLX
    OMLX(httpserver, wants_key=True, computes=computes)
    return _instance(tmp_path, monkeypatch,
                     f'[ai]\nprovider = "external"\nopenai_url = "{httpserver.url_for("/v1")}"\n'
                     f'openai_key = "{APP_KEY}"\ndefault = "{MODEL}"\n')


def test_check_says_the_ai_answers_with_its_model(tmp_path, monkeypatch, as_stack_runs_it,
                                                  httpserver):
    stack = _checked(tmp_path, monkeypatch, httpserver, computes=True)

    ran = stack("ai", "check", "--json")

    assert ran.code == 0, ran.err
    assert ran.answer["ok"] is True and ran.answer["model"] == MODEL
    assert MODEL in stack("ai", "check").out


def test_check_says_the_server_error_when_the_ai_cannot_answer(tmp_path, monkeypatch,
                                                               as_stack_runs_it, httpserver):
    """The server lists its models and fails each completion: the check
    fails, with the server's own error in one line."""
    from tests.unit.stacklets.omlx_stand_in import BROKEN
    stack = _checked(tmp_path, monkeypatch, httpserver, computes=False)

    ran = stack("ai", "check", "--json")

    assert ran.code == 1
    assert ran.answer["ok"] is False and ran.answer["model"] == MODEL
    assert ran.answer["server_error"] == f"HTTP 500: {BROKEN}"
    assert BROKEN in ran.answer["error"]


def test_check_in_the_terminal_names_the_model_or_the_error(tmp_path, monkeypatch,
                                                            as_stack_runs_it, httpserver):
    from tests.unit.stacklets.omlx_stand_in import BROKEN
    stack = _checked(tmp_path, monkeypatch, httpserver, computes=False)

    ran = stack("ai", "check")

    assert ran.code == 1 and BROKEN in ran.err


def test_check_keeps_the_answer_for_the_setup_page(tmp_path, monkeypatch, as_stack_runs_it,
                                                   httpserver):
    """`stack ai check` keeps its answer in the instance: the setup page's
    read of the AI (`stack ai check --last`) returns it, as long as
    `[ai]` names the same address, model and key."""
    stack = _checked(tmp_path, monkeypatch, httpserver, computes=False)

    stack("ai", "check", "--json")
    kept = stack("ai", "check", "--last", "--json").answer["answer"]
    (stack.instance / "stack.toml").write_text(
        stack.toml().replace(f'default = "{MODEL}"', 'default = "another-model"'))
    changed = stack("ai", "check", "--last", "--json").answer["answer"]

    assert kept["ok"] is False and kept["reason"] == "server" and kept["model"] == MODEL
    assert changed is None


def test_switch_asks_the_new_server_and_keeps_its_answer(tmp_path, monkeypatch,
                                                         as_stack_runs_it, httpserver):
    """`stack ai switch <url>` asks the server one short question once it
    wrote `[ai]`, and keeps the answer: a server that lists its models
    and fails every completion is stored, with the server's error."""
    from tests.unit.stacklets.omlx_stand_in import KEY as APP_KEY
    from tests.unit.stacklets.omlx_stand_in import OMLX
    app = OMLX(httpserver, wants_key=True, computes=False, downloaded=["Qwen3.5-9B-MLX-8bit"])
    monkeypatch.setattr(stack.docker, "project_states", lambda: {})
    cli = _instance(tmp_path, monkeypatch)

    ran = cli("ai", "switch", httpserver.url_for("/v1"), "--key", "-", "--json",
                stdin=APP_KEY)
    kept = cli("ai", "check", "--last", "--json").answer["answer"]

    assert ran.code == 0, ran.err
    assert app.completed == [] and kept["ok"] is False and kept["reason"] == "server"
