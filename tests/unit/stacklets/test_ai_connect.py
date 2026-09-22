"""`stack ai connect <url>`: choose the AI server the stack uses.

A household may run its models on another machine on the network, or
with a hosted provider. Pointing the stack there is one command, and it
must not need the local AI stacklet installed: that is the whole point.
`stack ai connect local` goes back to the engine on this Mac.

The command runs through the same path as `./stack ai connect`, against
the real stacklets and a throwaway instance, with a real HTTP server
standing in for the AI machine. Docker is the one thing it does not ask:
which stacklets are running is replaced, so the answer does not depend
on what happens to be up on the Mac running the tests.
"""

from __future__ import annotations

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
    """An OpenAI-compatible server listing `models`. Like oMLX, it has
    the transcription route whether or not a speech model is loaded."""
    httpserver.expect_request(f"{prefix}/models").respond_with_json(
        {"data": [{"id": m} for m in models]})
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


def _connect(instance, *args):
    return instance.run_cli_command("ai", "connect", list(args))


def _ai(instance):
    return instance.config["ai"]


class TestPointingTheStackElsewhere:

    def test_the_address_is_written_the_way_the_clients_need_it(self, instance, ai_server):
        """People write `host:port`; the clients need a scheme and `/v1`."""
        address = ai_server.url_for("/").removeprefix("http://").rstrip("/")

        result = _connect(instance, address)

        assert "error" not in result, result
        assert _ai(instance)["openai_url"] == ai_server.url_for("/v1")
        assert _ai(instance)["provider"] == "external"

    def test_the_only_chat_model_the_server_has_becomes_the_default(self, instance, ai_server):
        """The installer picked an MLX model this server has never heard
        of. Keeping it would fail every request, silently for the family.
        The speech model is not a candidate: it cannot answer a question."""
        _connect(instance, ai_server.url_for("/v1"))

        assert _ai(instance)["default"] == "llama3.1:8b"

    def test_a_server_without_auth_still_gets_a_key(self, instance, ai_server):
        """The OpenAI clients refuse an empty key."""
        _connect(instance, ai_server.url_for("/v1"))

        assert _ai(instance)["openai_key"]

    def test_the_admins_commented_alternatives_survive(self, instance, ai_server):
        _connect(instance, ai_server.url_for("/v1"))

        text = (instance.instance_dir / "stack.toml").read_text()
        assert '# default = "mlx-community/Qwen2.5-14B-Instruct-4bit"  # 64 GB' in text
        tomllib.loads(text)

    def test_it_names_the_running_stacklets_to_restart(self, instance, ai_server):
        """Nothing restarts on its own. Only stacklets that read the AI
        address and are running need it. The archivist runs in core, so
        docs reads no AI setting of its own, messages none at all, and the ai
        stacklet only its speech voice, which this command leaves alone."""
        result = _connect(instance, ai_server.url_for("/v1"))

        assert result["restart"] == ["core", "memory"]

    def test_a_server_at_home_raises_no_privacy_warning(self, instance, ai_server):
        result = _connect(instance, ai_server.url_for("/v1"))

        assert "warnings" not in result


class TestChoosingAModel:

    @pytest.fixture
    def many_models(self, httpserver):
        return _serve(httpserver, ["llama3.1:8b", "qwen3:14b"])

    def test_several_models_and_no_choice_asks_for_one_and_writes_nothing(
            self, instance, many_models):
        result = _connect(instance, many_models.url_for("/v1"))

        assert "--model" in result["error"]
        assert result["models"] == ["llama3.1:8b", "qwen3:14b"]
        assert _ai(instance)["openai_url"] == ""

    def test_a_chosen_model_the_server_has(self, instance, many_models):
        _connect(instance, many_models.url_for("/v1"), "--model", "qwen3:14b")

        assert _ai(instance)["default"] == "qwen3:14b"

    def test_a_chosen_model_the_server_lacks_is_refused(self, instance, many_models):
        result = _connect(instance, many_models.url_for("/v1"), "--model", "gpt-4.1")

        assert "gpt-4.1" in result["error"]
        assert _ai(instance)["openai_url"] == ""


class TestServersThatDoNotAnswer:

    def test_nothing_listening_writes_nothing(self, instance):
        result = _connect(instance, "http://127.0.0.1:9")

        assert "Nothing answers" in result["error"]
        assert _ai(instance)["openai_url"] == ""

    def test_a_server_that_wants_a_key_says_so(self, instance, httpserver):
        httpserver.expect_request("/v1/models").respond_with_data("", status=401)

        result = _connect(instance, httpserver.url_for("/v1"))

        assert "--key" in result["error"]

    def test_the_key_is_sent_and_stored(self, instance, httpserver):
        httpserver.expect_request(
            "/v1/models", headers={"Authorization": "Bearer sk-test"},
        ).respond_with_json({"data": [{"id": "gpt-4.1-mini"}]})

        _connect(instance, httpserver.url_for("/v1"), "--key", "sk-test")

        assert _ai(instance)["openai_key"] == "sk-test"


class TestBackToTheLocalEngine:

    def test_local_without_the_engine_installed_says_how_to_get_it(self, instance):
        result = _connect(instance, "local")

        assert "./stack up ai" in result["error"]
        assert _ai(instance)["openai_url"] == ""


class TestVoiceMessages:
    """A speech server, once set, gets the voice messages whatever the AI
    server offers. Without one the AI server gets them, and can only
    transcribe with a speech-to-text model loaded. Having the route is
    not enough: oMLX answers it with none and fails every voice message
    with "model not found"."""

    def test_without_a_speech_server_the_ai_servers_speech_model_is_used(
            self, instance, ai_server):
        result = _connect(instance, ai_server.url_for("/v1"))

        assert result["whisper_url"] == ai_server.url_for("/v1")
        assert _ai(instance)["whisper_url"] == ""
        assert _ai(instance)["whisper_model"] == "whisper-large-v3-turbo"
        assert "warnings" not in result

    def test_an_ai_server_without_a_speech_model_is_named(self, instance, httpserver):
        """The oMLX case: the route answers, nothing can transcribe."""
        _serve(httpserver, ["llama3.1:8b"])

        result = _connect(instance, httpserver.url_for("/v1"))

        assert any("no speech-to-text model" in w for w in result["warnings"])

    def test_whisper_ai_without_a_speech_model_is_refused_and_writes_nothing(
            self, instance, httpserver):
        _serve(httpserver, ["llama3.1:8b"])
        instance._set_cfg("ai", "whisper_url", "http://localhost:42062/v1")

        result = _connect(instance, httpserver.url_for("/v1"), "--whisper", "ai")

        assert "no speech-to-text model" in result["error"]
        assert _ai(instance)["whisper_url"] == "http://localhost:42062/v1"
        assert _ai(instance)["openai_url"] == ""

    def test_a_dedicated_speech_server(self, instance, httpserver):
        _serve(httpserver, ["llama3.1:8b"])
        stt = _speech_server(httpserver)

        result = _connect(instance, httpserver.url_for("/v1"), "--whisper", stt)

        assert _ai(instance)["whisper_url"] == stt
        assert "warnings" not in result

    def test_a_speech_server_that_does_not_answer_is_named(self, instance, ai_server):
        result = _connect(instance, ai_server.url_for("/v1"),
                          "--whisper", "http://127.0.0.1:9")

        assert any("127.0.0.1:9" in w for w in result["warnings"])

    def test_a_speech_server_already_set_wins_over_the_ai_servers_model(
            self, instance, ai_server):
        """Voice on this Mac, text elsewhere, is a choice worth keeping.
        The AI server's speech model is mentioned, not switched to."""
        stt = _speech_server(ai_server)
        instance._set_cfg("ai", "whisper_url", stt)

        result = _connect(instance, ai_server.url_for("/v1"))

        assert _ai(instance)["whisper_url"] == stt
        assert "whisper_model" not in _ai(instance)
        assert any("--whisper ai" in n for n in result["notes"])

    def test_whisper_ai_sends_voice_to_the_ai_server_again(self, instance, ai_server):
        instance._set_cfg("ai", "whisper_url", "http://localhost:42062/v1")
        instance._set_cfg("ai", "whisper_key", "old")

        result = _connect(instance, ai_server.url_for("/v1"), "--whisper", "ai")

        assert _ai(instance)["whisper_url"] == ""
        assert _ai(instance)["whisper_key"] == ""
        assert _ai(instance)["whisper_model"] == "whisper-large-v3-turbo"
        assert result["whisper_url"] == ai_server.url_for("/v1")
