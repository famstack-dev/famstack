"""Where voice messages go, as the stacklets that transcribe them see it.

Transcription is the OpenAI `/audio/transcriptions` call, which oMLX and
the hosted providers answer as well as a Whisper server does. So voice
messages go to the AI server, with its key, unless `[ai] whisper_url`
names a dedicated speech server; then that server gets them, with its
own key and never the AI key. `stack up ai` sets the local Whisper as
that dedicated server when it builds it.

The rendered environment is what the containers actually use, so that
is what these read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stack import Stack
from stack.ai.voice import speech_request, voice_for, voices

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def instance(tmp_path):
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\n\n[ai]\nlanguage = "en"\n'
        '\n[messages]\nserver_name = "simpson"\n')
    (tmp_path / ".stack").mkdir()
    return Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path)


class TestWhatTheStackletsGet:

    @pytest.mark.parametrize("stacklet", ["core", "memory", "chatai"])
    def test_without_a_speech_server_voice_goes_to_the_ai_server_with_its_key(
            self, instance, stacklet):
        instance._set_cfg("ai", "openai_url", "https://ai.example.test/v1")
        instance._set_cfg("ai", "openai_key", "sk-test")

        env = instance.env(stacklet)
        whisper = {k: v for k, v in env.items() if "STT" in k or "WHISPER" in k}

        assert any(v.startswith("https://ai.example.test/v1") for v in whisper.values())
        assert "sk-test" in whisper.values()

    @pytest.mark.parametrize("stacklet", ["core", "memory", "chatai"])
    def test_a_speech_server_gets_its_own_key_and_never_the_ai_key(
            self, instance, stacklet):
        instance._set_cfg("ai", "openai_key", "sk-test")
        instance._set_cfg("ai", "whisper_url", "https://stt.example.test/v1")
        instance._set_cfg("ai", "whisper_key", "stt-key")

        env = instance.env(stacklet)
        whisper = {k: v for k, v in env.items() if "STT" in k or "WHISPER" in k}

        assert "stt-key" in whisper.values()
        assert "sk-test" not in whisper.values()

    @pytest.mark.parametrize("stacklet", ["core", "memory", "chatai"])
    def test_the_speech_model_reaches_every_stacklet_that_transcribes(
            self, instance, stacklet):
        instance._set_cfg("ai", "whisper_model", "whisper-large-v3-turbo")

        env = instance.env(stacklet)

        assert "whisper-large-v3-turbo" in env.values()

    def test_an_emptied_speech_key_is_not_sent_as_an_empty_key(self, instance):
        """`stack ai switch --whisper ai` clears the key; the clients
        refuse an empty one, so it renders as the placeholder."""
        instance._set_cfg("ai", "whisper_url", "http://localhost:42062/v1")
        instance._set_cfg("ai", "whisper_key", "")

        assert instance.env("core")["WHISPER_KEY"] == "local"


class TestWhereSpeakingGoes:
    """What the bots speak with: the AI server, which on oMLX speaks as
    well as it writes, with its key; or the server `[ai] speech_url` names,
    with `[ai] speech_key` and never the AI key. As the containers reach
    it, and as a server root: the bots add `/v1/audio/speech`."""

    def test_by_default_the_bots_speak_through_the_ai_server_with_its_key(self, instance):
        instance._set_cfg("ai", "openai_url", "http://localhost:42060/v1")
        instance._set_cfg("ai", "openai_key", "omlx-key")

        env = instance.env("core")

        assert env["SPEECH_URL"] == "http://host.docker.internal:42060"
        assert env["SPEECH_KEY"] == "omlx-key"

    def test_a_speech_server_elsewhere_is_used_with_its_own_key(self, instance):
        instance._set_cfg("ai", "openai_key", "omlx-key")
        instance._set_cfg("ai", "speech_url", "https://tts.example.test")
        instance._set_cfg("ai", "speech_key", "tts-key")

        env = instance.env("core")

        assert env["SPEECH_URL"] == "https://tts.example.test"
        assert env["SPEECH_KEY"] == "tts-key"

    def test_a_speech_server_without_a_key_never_gets_the_ai_key(self, instance):
        instance._set_cfg("ai", "openai_key", "omlx-key")
        instance._set_cfg("ai", "speech_url", "https://tts.example.test/v1")

        env = instance.env("core")

        assert env["SPEECH_URL"] == "https://tts.example.test"
        assert env["SPEECH_KEY"] == ""


class TestWhichVoicesTheBotsSpeakWith:
    """The bots read no stack.toml, so they get the voices in force as
    JSON: `[ai.voices]` over the stack's own. A voice message built from
    it asks for exactly what `stack ai say` asks for."""

    def test_by_default_the_bots_get_the_stacks_own_voices(self, instance):
        table = voices(json.loads(instance.env("core")["SPEECH_VOICES_JSON"]))

        assert (table["en"].model, table["en"].speaker) == ("Kokoro-82M-bf16", "af_heart")
        assert speech_request("Hallo.", voice_for(table, "de"))["ref_text"].startswith("Stell")

    def test_a_voice_named_in_stack_toml_reaches_the_bots_as_named(self, instance):
        with open(instance.instance_dir / "stack.toml", "a") as f:
            f.write('\n[ai.voices.de]\nmodel = "tts-1"\nvoice = "onyx"\n')

        table = voices(json.loads(instance.env("core")["SPEECH_VOICES_JSON"]))

        assert speech_request("Hallo.", voice_for(table, "de"), response_format="opus") == {
            "model": "tts-1", "voice": "onyx", "input": "Hallo.", "speed": 0.8,
            "response_format": "opus"}
        assert table["en"].speaker == "af_heart"
