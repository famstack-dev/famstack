"""`stack ai switch <target>`: choose the AI server the stack uses.

A household may run its models on another machine on the network, in an
AI app on the server Mac, or with a hosted provider. Pointing the stack
there is one command, and it must not need the ai stacklet installed:
that is the whole point. `stack ai switch managed` moves it to the
engine the stack runs on this Mac.

The command runs through the same path as `./stack ai switch`, against
the real stacklets and a throwaway instance, with a real HTTP server
standing in for the AI machine. Docker is the one thing it does not ask:
which stacklets are running is replaced, so the answer does not depend
on what happens to be up on the Mac running the tests.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from stack import Stack
import stack.docker

REPO_ROOT = Path(__file__).resolve().parents[3]

# What the installer writes: no speech server of its own until
# `stack up ai` builds one.
GENERATED = '''\
[core]
data_dir = "{data}"

[ai]
provider = ""
openai_url = ""
openai_key = ""
language = "en"
# Models by RAM tier, uncomment one to switch:
default = "mlx-community/Qwen3.5-9B-MLX-8bit"  # 32 GB (selected)
# default = "mlx-community/Qwen2.5-14B-Instruct-4bit"  # 64 GB

[messages]
server_name = "simpson"
'''


@pytest.fixture
def instance(tmp_path, monkeypatch):
    """A fresh install that never ran `stack up ai`, with core, docs,
    memory and messages running, and the local speech container of the ai
    stacklet."""
    (tmp_path / "stack.toml").write_text(GENERATED.format(data=tmp_path / "data"))
    (tmp_path / ".stack").mkdir()
    monkeypatch.setattr(stack.docker, "project_states", lambda: {
        "ai": "running", "core": "running", "docs": "running",
        "memory": "running", "messages": "running"})
    return Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path)


def _serve(httpserver, models, prefix="/v1"):
    """An OpenAI-compatible server listing `models`, which answers the
    switch's short question. Like oMLX, it has the transcription route
    whether or not a speech model is loaded."""
    httpserver.expect_request(f"{prefix}/models").respond_with_json(
        {"data": [{"id": m} for m in models]})
    httpserver.expect_request(f"{prefix}/chat/completions", method="POST").respond_with_json(
        {"choices": [{"message": {"role": "assistant", "content": "OK"}}]})
    httpserver.expect_request(
        f"{prefix}/audio/transcriptions", method="POST",
    ).respond_with_data("file is required", status=422)
    return httpserver


def _speech_server(httpserver, prefix="/stt/v1"):
    """whisper.cpp: the transcription route, no model list."""
    httpserver.expect_request(
        f"{prefix}/audio/transcriptions", method="POST",
    ).respond_with_data("file is required", status=422)
    return httpserver.url_for(prefix)


@pytest.fixture
def ai_server(httpserver):
    """A machine on the network with one chat model and a speech model."""
    return _serve(httpserver, ["llama3.1:8b", "whisper-large-v3-turbo"])


def _switch(instance, *args):
    return instance.run_cli_command("ai", "switch", list(args))


def _ai(instance):
    return instance.config["ai"]


class TestPointingTheStackElsewhere:

    def test_the_address_is_written_the_way_the_clients_need_it(self, instance, ai_server):
        """People write `host:port`; the clients need a scheme and `/v1`."""
        address = ai_server.url_for("/").removeprefix("http://").rstrip("/")

        result = _switch(instance, address)

        assert "error" not in result, result
        assert _ai(instance)["openai_url"] == ai_server.url_for("/v1")
        assert _ai(instance)["provider"] == "external"

    def test_the_only_chat_model_the_server_has_becomes_the_default(self, instance, ai_server):
        """The installer picked an MLX model this server has never heard
        of. Keeping it would fail every request, silently for the family.
        The speech model is not a candidate: it cannot answer a question."""
        _switch(instance, ai_server.url_for("/v1"))

        assert _ai(instance)["default"] == "llama3.1:8b"

    def test_a_server_without_auth_still_gets_a_key(self, instance, ai_server):
        """The OpenAI clients refuse an empty key."""
        _switch(instance, ai_server.url_for("/v1"))

        assert _ai(instance)["openai_key"]

    def test_the_admins_commented_alternatives_survive(self, instance, ai_server):
        _switch(instance, ai_server.url_for("/v1"))

        text = (instance.instance_dir / "stack.toml").read_text()
        assert '# default = "mlx-community/Qwen2.5-14B-Instruct-4bit"  # 64 GB' in text
        tomllib.loads(text)

    def test_it_names_the_running_stacklets_to_restart(self, instance, ai_server):
        """Nothing restarts on its own. Only stacklets that read the AI
        address and are running need it. The archivist runs in core, so
        docs reads no AI setting of its own, messages none at all, and the ai
        stacklet only its speech voice, which this command leaves alone."""
        result = _switch(instance, ai_server.url_for("/v1"))

        assert result["restart"] == ["core", "memory"]

    def test_a_server_at_home_raises_no_privacy_warning(self, instance, ai_server):
        result = _switch(instance, ai_server.url_for("/v1"))

        assert "warnings" not in result


class TestTheManagedEngineLeftRunning:

    def test_switching_away_says_the_managed_engine_can_be_stopped(
            self, instance, ai_server):
        """oMLX keeps using memory for nothing."""
        instance._set_cfg("ai", "provider", "managed")

        result = _switch(instance, ai_server.url_for("/v1"))

        assert any("./stack down ai" in n for n in result["notes"])

    def test_voice_on_this_mac_is_not_called_unused(self, instance, ai_server):
        """Chat moves between servers while the ai stacklet keeps serving
        speech here: nothing of it is idle."""
        instance._set_cfg("ai", "provider", "external")

        result = _switch(instance, ai_server.url_for("/v1"))

        assert not any("./stack down ai" in n for n in result.get("notes", []))


class TestChoosingAModel:

    @pytest.fixture
    def many_models(self, httpserver):
        return _serve(httpserver, ["llama3.1:8b", "qwen3:14b"])

    def test_several_models_and_no_choice_asks_for_one_and_writes_nothing(
            self, instance, many_models):
        result = _switch(instance, many_models.url_for("/v1"))

        assert "--model" in result["error"]
        assert result["models"] == ["llama3.1:8b", "qwen3:14b"]
        # In a terminal only the error line is printed, so it names them.
        assert "llama3.1:8b" in result["error"] and "qwen3:14b" in result["error"]
        assert _ai(instance)["openai_url"] == ""

    def test_a_chosen_model_the_server_has(self, instance, many_models):
        _switch(instance, many_models.url_for("/v1"), "--model", "qwen3:14b")

        assert _ai(instance)["default"] == "qwen3:14b"

    def test_a_chosen_model_the_server_lacks_is_refused(self, instance, many_models):
        result = _switch(instance, many_models.url_for("/v1"), "--model", "gpt-4.1")

        assert "gpt-4.1" in result["error"]
        assert _ai(instance)["openai_url"] == ""


class TestServersThatDoNotAnswer:

    def test_nothing_listening_writes_nothing(self, instance):
        result = _switch(instance, "http://127.0.0.1:9")

        assert "Nothing answers" in result["error"]
        assert _ai(instance)["openai_url"] == ""

    def test_a_server_that_wants_a_key_says_so(self, instance, httpserver):
        httpserver.expect_request("/v1/models").respond_with_data("", status=401)

        result = _switch(instance, httpserver.url_for("/v1"))

        assert "--key" in result["error"]

    def test_the_key_is_sent_and_stored(self, instance, httpserver):
        httpserver.expect_request(
            "/v1/models", headers={"Authorization": "Bearer sk-test"},
        ).respond_with_json({"data": [{"id": "gpt-4.1-mini"}]})

        _switch(instance, httpserver.url_for("/v1"), "--key", "sk-test")

        assert _ai(instance)["openai_key"] == "sk-test"


@pytest.fixture
def homebrew_fails(tmp_path, monkeypatch):
    """A Mac whose Homebrew fails every command and whose home directory is
    the test's: a real `stack up ai` stops at its first `brew`, before
    anything is installed or written into the home directory."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "brew").write_text("#!/bin/sh\nexit 1\n")
    (bin_dir / "brew").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


class TestTheManagedEngine:
    """`managed` brings up the engine the stack runs, through `stack up ai`,
    which installs what is missing. The real command runs here, so it runs
    on a Mac whose Homebrew fails every command and whose home directory is
    the test's: the install stops at its first `brew`, before anything is
    installed, which is the failure this pins.

    Nothing else stops it. `stack ai switch managed` sets the provider
    before `stack up ai`, so the stacklet has no setup question left
    (ADR-021); without the failing Homebrew this test installed oMLX's
    settings and a Whisper service into the home directory running it."""

    @pytest.fixture(autouse=True)
    def _homebrew_fails(self, homebrew_fails):
        pass

    def test_when_it_cannot_come_up_the_stack_keeps_its_server(self, instance, ai_server):
        _switch(instance, ai_server.url_for("/v1"))
        before = dict(_ai(instance))

        result = _switch(instance, "managed")

        assert "still uses " + ai_server.url_for("/v1") in result["error"]
        assert _ai(instance) == before

    def test_while_another_run_goes_on_it_names_that_run_and_keeps_the_server(
            self, instance, ai_server, monkeypatch):
        """Through the host API (the setup page) a run does not wait its
        turn: while a run of vault goes on, `stack up ai` is refused at once,
        and the answer names that run instead of a failure to look up."""
        from stack import runs
        _switch(instance, ai_server.url_for("/v1"))
        before = dict(_ai(instance))
        monkeypatch.setenv(runs.QUEUE_VARIABLE, "no")

        with runs.hold(instance.instance_dir, ["vault"]):
            result = _switch(instance, "managed")

        assert "another run goes on" in result["error"]
        assert "vault" in result["error"]
        assert _ai(instance) == before

    def test_a_chosen_model_is_put_back_with_the_rest(self, instance, ai_server):
        """The model goes into `[ai]` before the engine comes up, since
        the install downloads it, and back out when that fails."""
        _switch(instance, ai_server.url_for("/v1"))
        before = dict(_ai(instance))

        _switch(instance, "managed", "--model", "mlx-community/Qwen3.5-9B-MLX-4bit")

        assert _ai(instance) == before


class TestTheManagedEngineThatComesUp:
    """`managed` once its run succeeded. The run (`stack up ai`) is stood in
    where the switch starts it, `local_mode.bring_up`: it gives `[ai]`
    famstack's own key, as the install does, asks the engine one short
    question and keeps the answer, as the run ends. The engine is an oMLX
    stand-in that lists its models by folder name, as oMLX does."""

    KEY = "kq7m-2xpt-r9fd-h3wa"

    @pytest.fixture
    def engine(self, instance, httpserver, monkeypatch, tmp_path, homebrew_fails):
        import importlib

        from stacklets.ai import answer
        # The module the command imports, from `sys.modules`: the package's
        # attribute can name a copy an earlier command's block forgot.
        local_mode = importlib.import_module("stacklets.ai.local_mode")

        _serve(httpserver, ["Qwen3.5-9B-MLX-8bit"])
        monkeypatch.setitem(local_mode.MANAGED_ENGINE, "openai_url", httpserver.url_for("/v1"))
        # The engine's own speech server, when speech is on.
        monkeypatch.setitem(local_mode.MANAGED_ENGINE, "whisper_url", _speech_server(httpserver))

        def run(config) -> int:
            instance._set_cfg("ai", "openai_key", self.KEY)
            ai = tomllib.loads((tmp_path / "stack.toml").read_text())["ai"]
            assert answer.check(tmp_path, ai)["ok"]
            return 0

        monkeypatch.setattr(local_mode, "bring_up", run)
        return httpserver

    def test_the_page_knows_the_answer_the_run_ended_with(self, instance, engine):
        """The run asked the engine with `[ai]` as it left it. The switch
        then writes nothing that changes that `[ai]` (the model as oMLX
        lists it, another key), so the setup page reads the run's answer
        for the `[ai]` it finds."""
        _switch(instance, "managed")

        kept = instance.run_cli_command("ai", "check", ["--last"])["answer"]
        assert kept is not None and kept["ok"] is True, _ai(instance)
        assert _ai(instance)["default"] == "mlx-community/Qwen3.5-9B-MLX-8bit"
        assert _ai(instance)["openai_key"] == self.KEY

    def test_with_speech_off_no_voice_server_is_set_and_nothing_warns(self, instance, engine):
        """A small Mac starts with speech off. The engine then gets no
        speech server, and the switch does not warn that voice messages
        will fail: they are off."""
        instance._set_cfg("ai", "speech", False)

        result = _switch(instance, "managed")

        assert "error" not in result, result
        assert _ai(instance).get("whisper_url", "") == ""
        assert not any(r.path.endswith("/audio/transcriptions") for r, _ in engine.log)
        assert not any("voice" in w.lower() for w in result.get("warnings", [])), result


class TestVoiceMessages:
    """A speech server, once set, gets the voice messages whatever the AI
    server offers. Without one the AI server gets them, and can only
    transcribe with a speech-to-text model loaded. Having the route is
    not enough: oMLX answers it with none and fails every voice message
    with "model not found"."""

    def test_without_a_speech_server_the_ai_servers_speech_model_is_used(
            self, instance, ai_server):
        result = _switch(instance, ai_server.url_for("/v1"))

        assert result["whisper_url"] == ai_server.url_for("/v1")
        assert _ai(instance)["whisper_url"] == ""
        assert _ai(instance)["whisper_model"] == "whisper-large-v3-turbo"
        assert "warnings" not in result

    def test_an_ai_server_without_a_speech_model_is_named(self, instance, httpserver):
        """The oMLX case: the route answers, nothing can transcribe."""
        _serve(httpserver, ["llama3.1:8b"])

        result = _switch(instance, httpserver.url_for("/v1"))

        assert any("no speech-to-text model" in w for w in result["warnings"])

    def test_whisper_ai_without_a_speech_model_is_refused_and_writes_nothing(
            self, instance, httpserver):
        _serve(httpserver, ["llama3.1:8b"])
        instance._set_cfg("ai", "whisper_url", "http://localhost:42062/v1")

        result = _switch(instance, httpserver.url_for("/v1"), "--whisper", "ai")

        assert "no speech-to-text model" in result["error"]
        assert _ai(instance)["whisper_url"] == "http://localhost:42062/v1"
        assert _ai(instance)["openai_url"] == ""

    def test_a_dedicated_speech_server(self, instance, httpserver):
        _serve(httpserver, ["llama3.1:8b"])
        stt = _speech_server(httpserver)

        result = _switch(instance, httpserver.url_for("/v1"), "--whisper", stt)

        assert _ai(instance)["whisper_url"] == stt
        assert "warnings" not in result

    def test_a_speech_server_that_does_not_answer_is_named(self, instance, ai_server):
        result = _switch(instance, ai_server.url_for("/v1"),
                          "--whisper", "http://127.0.0.1:9")

        assert any("127.0.0.1:9" in w for w in result["warnings"])

    def test_a_speech_server_already_set_wins_over_the_ai_servers_model(
            self, instance, ai_server):
        """Voice on this Mac, text elsewhere, is a choice worth keeping.
        The AI server's speech model is mentioned, not switched to."""
        stt = _speech_server(ai_server)
        instance._set_cfg("ai", "whisper_url", stt)

        result = _switch(instance, ai_server.url_for("/v1"))

        assert _ai(instance)["whisper_url"] == stt
        assert "whisper_model" not in _ai(instance)
        assert any("--whisper ai" in n for n in result["notes"])

    def test_whisper_ai_sends_voice_to_the_ai_server_again(self, instance, ai_server):
        instance._set_cfg("ai", "whisper_url", "http://localhost:42062/v1")
        instance._set_cfg("ai", "whisper_key", "old")

        result = _switch(instance, ai_server.url_for("/v1"), "--whisper", "ai")

        assert _ai(instance)["whisper_url"] == ""
        assert _ai(instance)["whisper_key"] == ""
        assert _ai(instance)["whisper_model"] == "whisper-large-v3-turbo"
        assert result["whisper_url"] == ai_server.url_for("/v1")


# ── While the oMLX app runs ──────────────────────────────────────────────

@pytest.fixture
def omlx_app_runs(tmp_path, monkeypatch):
    """A Mac where the oMLX app runs: a stand-in `pgrep` finds its process,
    and stand-ins for `brew` and `launchctl` write down every call, so a
    test sees whether anything was installed or started. On PATH, so the
    real `stack up ai` that the switch runs uses them too. The lane counts
    the app as closed (`omlx_app_closed`); this puts the real check back.
    Returns the file of calls."""
    from stacklets.ai import omlx_app
    monkeypatch.setattr(omlx_app, "PROCESS_COMMAND", ("pgrep", "-f", omlx_app.IN_THE_APP))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    calls.write_text("")
    (bin_dir / "pgrep").write_text(
        '#!/bin/sh\ncase "$*" in *oMLX.app/Contents/*) echo 4242; exit 0;; esac\nexit 1\n')
    for tool in ("brew", "launchctl"):
        (bin_dir / tool).write_text(f'#!/bin/sh\necho "{tool} $*" >> "{calls}"\nexit 1\n')
    for tool in ("pgrep", "brew", "launchctl"):
        (bin_dir / tool).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return calls


QUIT_THE_APP = "Quit the oMLX app first"


class TestFamstacksOmlxNeverStartsBesideTheApp:
    """Two MLX servers on one Mac's graphics chip can crash the Mac. So
    while the oMLX app runs, famstack's oMLX is neither installed nor
    started: the run stops first, says to quit the app, and changes
    nothing. The setup page shows the run's message on the AI's tile."""

    def test_the_switch_to_famstacks_omlx_stops_and_changes_nothing(
            self, instance, ai_server, omlx_app_runs, capfd):
        """The terminal shows the run's message: quit the app first."""
        _switch(instance, ai_server.url_for("/v1"))
        before = (instance.instance_dir / "stack.toml").read_text()
        capfd.readouterr()

        result = _switch(instance, "managed")

        assert "error" in result, result
        assert QUIT_THE_APP in capfd.readouterr().err
        assert (instance.instance_dir / "stack.toml").read_text() == before
        assert omlx_app_runs.read_text() == ""

    def test_the_ais_start_stops_with_the_reason_and_starts_nothing(
            self, instance, omlx_app_runs, monkeypatch):
        """An instance on famstack's oMLX, set up before: its start is the
        run the page follows, and its result carries the message. The
        start first looks for an old speech container, which Docker would
        answer: there is none."""
        monkeypatch.setattr(stack.docker, "containers_for", lambda _id: [])
        instance._set_cfg("ai", "provider", "managed")
        instance._set_cfg("ai", "openai_url", "http://localhost:42060/v1")
        (instance.instance_dir / ".stack" / "ai.setup-done").touch()

        result = instance.up("ai")

        assert QUIT_THE_APP in result.get("cancelled", ""), result
        assert "Nothing changed" in result["cancelled"]
        assert omlx_app_runs.read_text() == ""

    def test_a_first_run_stops_before_homebrew(self, instance, omlx_app_runs):
        """A first `stack up ai` on route 1 installs oMLX: it stops before
        the first `brew`."""
        instance._set_cfg("ai", "provider", "managed")
        instance._set_cfg("ai", "openai_url", "http://localhost:42060/v1")

        result = instance.up("ai")

        assert QUIT_THE_APP in result.get("cancelled", ""), result
        assert omlx_app_runs.read_text() == ""


def test_with_speech_off_a_servers_speech_model_is_not_used_or_named(
        instance, ai_server, capfd):
    """Given speech off and a server that also lists a speech model, then
    the switch sets no speech model and its summary names no voice
    messages: there are none."""
    instance._set_cfg("ai", "speech", False)

    result = _switch(instance, ai_server.url_for("/v1"))

    assert "error" not in result, result
    assert _ai(instance).get("whisper_model", "") == ""
    assert "Voice messages" not in capfd.readouterr().out
