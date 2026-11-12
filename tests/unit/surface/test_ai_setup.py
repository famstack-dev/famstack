"""The ai stacklet's setup question, before its first `stack up ai`.

On a fresh instance it asks whether to set up oMLX, the engine the stack
runs on this Mac; a no installs nothing and names `stack ai switch` for a
server elsewhere. After `stack ai switch <url>`, installing the stacklet
can mean two setups, so it asks whether the engine here takes over: yes
moves chat and voice onto it, no keeps that server for chat and installs
only the voice services, the setup of a Mac that serves its own AI app.
An installed stacklet asks nothing; its engine is chosen with
`stack ai switch`.

Before it offers oMLX, a fresh instance looks for an AI server already
running on this Mac (the oMLX app) and offers that one: a yes stores it as
`stack ai switch` does, so no second oMLX is installed next to it.

These tests run `./stack configure ai` in this process. Homebrew is stood
in by an empty `brew` first on PATH, since a yes to oMLX needs it. The
address where an AI server is looked for points at a port where nothing
listens, unless a test stands a server in there: what happens to run on
port 8000 of the Mac running the tests must not decide them.
"""

from __future__ import annotations

import tomllib

import pytest

import stack.docker

from .conftest import StackCLI, a_closed_port, write_instance

REMOTE = ('[ai]\nprovider = "external"\nopenai_url = "https://ai.example.test/v1"\n'
          'openai_key = "sk-test"\nwhisper_url = "https://stt.example.test/v1"\n'
          'whisper_key = "stt-key"\ndefault = "gpt-4.1-mini"\n')


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
def no_server_on_this_mac(monkeypatch, as_stack_runs_it):
    """Nothing answers where the stack looks for an AI server on this Mac,
    and Homebrew has no oMLX."""
    from stacklets.ai import homebrew_omlx, running_server
    monkeypatch.setattr(running_server, "RUNNING_SERVERS",
                        (f"http://127.0.0.1:{a_closed_port()}/v1",))
    monkeypatch.setattr(homebrew_omlx, "LIST_COMMAND", ("false",))


@pytest.fixture
def switched(tmp_path, monkeypatch, as_stack_runs_it) -> StackCLI:
    """An instance pointed at a remote AI server with `stack ai switch`."""
    return _instance(tmp_path, monkeypatch, REMOTE)


@pytest.fixture
def fresh(tmp_path, monkeypatch, as_stack_runs_it, brew, no_server_on_this_mac) -> StackCLI:
    """An instance with no `[ai]` yet, on a Mac with Homebrew."""
    return _instance(tmp_path, monkeypatch)


# ── After `stack ai switch` ──────────────────────────────────────────────

def test_the_question_names_a_remote_server(switched):
    form = switched("configure", "ai", "--json").answer["form"]

    assert form["id"] == "ai.engine"
    assert "a remote AI server (https://ai.example.test/v1)" in form["text"]


def test_a_server_on_this_mac_is_not_called_remote(tmp_path, monkeypatch, as_stack_runs_it):
    local = _instance(tmp_path, monkeypatch,
                      REMOTE.replace("https://ai.example.test/v1", "http://localhost:8888/v1"))

    text = local("configure", "ai", "--json").answer["form"]["text"]

    assert "on this Mac that the stack does not manage (http://localhost:8888/v1)" in text
    assert "remote" not in text


def test_no_keeps_the_server_and_goes_on_to_install_voice(switched):
    """The install that follows skips the engine for a server set with
    switch, and installs speech-to-text and text-to-speech."""
    before = switched.toml()

    ran = switched("configure", "ai", "--json", "--answer", "use_this_mac=no")

    assert ran.answer == {"ready": True}
    assert switched.toml() == before


def test_no_is_the_default(switched, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    before = switched.toml()

    ran = switched("configure", "ai", terminal=True)

    assert ran.code == 0, ran.err
    assert switched.toml() == before


def test_yes_switches_chat_and_voice_to_this_mac_together(switched):
    """Never one half remote and the other local."""
    switched("configure", "ai", "--json", "--answer", "use_this_mac=yes")

    toml = switched.toml()
    assert 'provider = "managed"' in toml
    assert 'openai_url = "http://localhost:42060/v1"' in toml
    assert 'whisper_url = "http://localhost:42062/v1"' in toml
    assert 'whisper_key = ""' in toml
    assert 'openai_key = "local"' in toml


def test_without_an_answer_nothing_is_installed_and_the_form_is_named(switched):
    """Nobody to answer: an agent or a script installing the stacklet must
    not change the family's AI setup."""
    before = switched.toml()

    ran = switched("up", "ai")

    assert ran.code == 1
    assert "ai.engine" in ran.err and "use_this_mac" in ran.err
    assert switched.toml() == before


def test_an_installed_stacklet_asks_nothing(switched):
    """Its engine is chosen with `stack ai switch` from then on."""
    (switched.instance / ".stack" / "ai.setup-done").write_text("")

    ran = switched("configure", "ai")

    assert ran.code == 0
    assert "Nothing to ask" in ran.out
    assert "sk-test" not in ran.out


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
                                                    no_server_on_this_mac):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    no_brew = _instance(tmp_path, monkeypatch)

    ran = no_brew("configure", "ai", "--answer", "setup_omlx=yes")

    assert ran.code == 1
    assert "Homebrew" in ran.err
    assert "provider" not in no_brew.toml()


# ── With an AI server already running on this Mac ────────────────────────

KEY = "omlx-app-key"
MODEL = "mlx-community/Qwen3.5-9B-MLX-8bit"


def _models(httpserver, key: str = ""):
    """An OpenAI-compatible server like the oMLX app, listing one model.
    With a key it answers only a request carrying that key."""
    listed = {"data": [{"id": MODEL}]}
    if key:
        httpserver.expect_request(
            "/v1/models", headers={"Authorization": f"Bearer {key}"}).respond_with_json(listed)
        httpserver.expect_request("/v1/models").respond_with_data("unauthorized", status=401)
    else:
        httpserver.expect_request("/v1/models").respond_with_json(listed)


def _omlx_admin(httpserver) -> None:
    """What makes the server the oMLX app: oMLX's admin API refuses a
    request without a login, and its API description names oMLX, as the
    oMLX app 0.7.3 answers."""
    httpserver.expect_request("/admin/api/models").respond_with_json(
        {"detail": "Admin authentication required"}, status=401)
    httpserver.expect_request("/openapi.json").respond_with_json(
        {"openapi": "3.1.0", "info": {"title": "oMLX API", "version": "0.7.3"}})


@pytest.fixture
def app_running(tmp_path, monkeypatch, as_stack_runs_it, brew, httpserver) -> StackCLI:
    """A fresh instance on a Mac where an AI server that wants a key
    answers at the address the stack looks at. Docker is asked which
    stacklets run, and answers none."""
    from stacklets.ai import running_server
    _models(httpserver, KEY)
    _omlx_admin(httpserver)
    monkeypatch.setattr(running_server, "RUNNING_SERVERS", (httpserver.url_for("/v1"),))
    monkeypatch.setattr(stack.docker, "project_states", lambda: {})
    return _instance(tmp_path, monkeypatch)


def test_a_server_running_on_this_mac_is_offered_before_omlx(app_running, httpserver):
    form = app_running("configure", "ai", "--json").answer["form"]

    assert form["id"] == "ai.running"
    assert httpserver.url_for("").rstrip("/") in form["text"]
    assert form["schema"]["properties"]["use_running_server"]["default"] is True


def test_its_key_is_asked_as_a_secret(app_running):
    """The dashboard on another device sends a secret to the terminal."""
    form = app_running("configure", "ai", "--json",
                       "--answer", "use_running_server=yes").answer["form"]

    assert form["id"] == "ai.running-key"
    assert form["schema"]["properties"]["api_key"]["writeOnly"] is True
    assert "oMLX app shows it under Settings, Security, API Key" in form["text"]


def test_yes_stores_the_server_and_installs_no_omlx(app_running, httpserver):
    """As `stack ai switch <address> --key <key>` stores it: the provider
    is the server the stack does not manage, so the install that follows
    skips oMLX."""
    ran = app_running("configure", "ai", "--json",
                      "--answer", "use_running_server=yes", "--answer", f"api_key={KEY}")

    assert ran.answer == {"ready": True}, ran.err
    toml = app_running.toml()
    assert 'provider = "external"' in toml
    assert f'openai_url = "{httpserver.url_for("/v1")}"' in toml
    assert f'openai_key = "{KEY}"' in toml
    assert f'default = "{MODEL}"' in toml
    assert "42060" not in toml


def test_a_server_without_a_key_needs_only_the_yes(tmp_path, monkeypatch, as_stack_runs_it,
                                                    brew, httpserver):
    from stacklets.ai import running_server
    _models(httpserver)
    _omlx_admin(httpserver)
    monkeypatch.setattr(running_server, "RUNNING_SERVERS", (httpserver.url_for("/v1"),))
    monkeypatch.setattr(stack.docker, "project_states", lambda: {})
    open_server = _instance(tmp_path, monkeypatch)

    ran = open_server("configure", "ai", "--json", "--answer", "use_running_server=yes")

    assert ran.answer == {"ready": True}, ran.err
    assert 'provider = "external"' in open_server.toml()


def test_no_asks_whether_to_set_up_omlx(app_running):
    form = app_running("configure", "ai", "--json",
                       "--answer", "use_running_server=no").answer["form"]

    assert form["id"] == "ai.omlx"


def test_a_refused_key_stores_nothing_and_the_next_start_asks_again(app_running):
    ran = app_running("configure", "ai", "--json",
                      "--answer", "use_running_server=yes", "--answer", "api_key=wrong-key")

    assert ran.code == 1
    assert "refused" in ran.err
    assert "wrong-key" not in ran.err + ran.out
    assert "provider" not in app_running.toml()
    assert app_running("configure", "ai", "--json").answer["form"]["id"] == "ai.running"


def test_with_a_provider_set_nothing_is_probed(tmp_path, monkeypatch, as_stack_runs_it,
                                               httpserver):
    """The engine is chosen already: the server running here is not offered."""
    from stacklets.ai import running_server
    monkeypatch.setattr(running_server, "RUNNING_SERVERS", (httpserver.url_for("/v1"),))
    managed = _instance(tmp_path, monkeypatch, '[ai]\nprovider = "managed"\n')

    ran = managed("configure", "ai")

    assert ran.code == 0
    assert "Nothing to ask" in ran.out
    assert httpserver.log == []


def test_stack_up_ai_with_yes_never_calls_homebrew(app_running, tmp_path, monkeypatch):
    """The whole first start, not only its questions: no oMLX is installed.
    The voice services are skipped (`STACK_AI_NO_VOICE`), as they are
    installed for any server the stack does not manage. The start goes on
    past the install and stops at the Docker network, which the unit lane
    does not have; the install is what this test is about."""
    calls = tmp_path / "brew-calls"
    (tmp_path / "bin" / "brew").write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    monkeypatch.setenv("STACK_AI_NO_VOICE", "1")

    app_running("up", "ai", "--answer", "use_running_server=yes", "--answer", f"api_key={KEY}")

    assert not calls.exists()
    assert 'provider = "external"' in app_running.toml()


def test_in_a_terminal_yes_is_the_default_and_the_key_is_read_hidden(app_running, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": KEY)

    ran = app_running("configure", "ai", terminal=True)

    assert ran.code == 0, ran.err
    assert f'openai_key = "{KEY}"' in app_running.toml()
    assert KEY not in ran.out + ran.err


# ── Speech stored off, as the install leaves it on a small Mac ───────────

@pytest.fixture
def speech_off(tmp_path, monkeypatch, as_stack_runs_it, brew, httpserver) -> StackCLI:
    """`app_running`, on an instance whose install stored `[ai] speech = false`.
    `brew` notes every call it gets: building Whisper starts with
    `brew list cmake`."""
    from stacklets.ai import running_server
    _models(httpserver, KEY)
    _omlx_admin(httpserver)
    monkeypatch.setattr(running_server, "RUNNING_SERVERS", (httpserver.url_for("/v1"),))
    monkeypatch.setattr(stack.docker, "project_states", lambda: {})
    monkeypatch.delenv("STACK_AI_NO_VOICE", raising=False)
    calls = tmp_path / "brew-calls"
    (tmp_path / "bin" / "brew").write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    return _instance(tmp_path, monkeypatch, "[ai]\nspeech = false\n")


def test_with_speech_stored_off_stack_up_ai_installs_no_whisper(speech_off, tmp_path):
    """Given `[ai] speech = false`, the first start behaves as `--no-voice`:
    no Whisper build, no speech server stored for voice messages."""
    speech_off("up", "ai", "--answer", "use_running_server=yes", "--answer", f"api_key={KEY}")

    assert not (tmp_path / "brew-calls").exists()
    assert not (tmp_path / "data" / "ai" / "whisper.cpp").exists()
    ai = tomllib.loads(speech_off.toml())["ai"]
    assert ai["provider"] == "external"
    assert ai.get("whisper_url", "") == ""


def test_once_the_app_on_this_mac_is_used_the_first_start_asks_nothing_more(app_running,
                                                                           httpserver):
    """The setup page connects the oMLX app, then starts `stack up ai`
    without a terminal: a question whether the stack's own engine takes
    over would stop that start, and its yes would put a second engine
    next to the app. The server is the app because it answers as oMLX
    does."""
    app_running("configure", "ai", "--json",
                "--answer", "use_running_server=yes", "--answer", f"api_key={KEY}")

    assert app_running("configure", "ai", "--json").answer == {"ready": True}


def test_the_app_is_known_by_what_answers_not_by_its_port(app_running, httpserver,
                                                          monkeypatch):
    """The app may listen on another port than its default one (it reads
    the port from the settings file the Homebrew oMLX shares). Connected
    there, the first start still asks nothing more: the stack knows the
    app by oMLX's admin API answering on this Mac, not by the address it
    looks at first."""
    from stacklets.ai import running_server
    app_running("configure", "ai", "--json",
                "--answer", "use_running_server=yes", "--answer", f"api_key={KEY}")
    monkeypatch.setattr(running_server, "RUNNING_SERVERS",
                        (f"http://127.0.0.1:{a_closed_port()}/v1",))

    assert app_running("configure", "ai", "--json").answer == {"ready": True}


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
