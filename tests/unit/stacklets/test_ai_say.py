"""`stack ai say <text>`: speak any text aloud on the Mac.

The admin types a sentence and hears it, through the ai stacklet's speech
server, in the default voice for `[ai] language` or in the one `--voice`
names. A voice the server does not know, no text, or a speech server
that is not running each end in an error that says what to do, and
nothing is played.

The command runs through the same path as `./stack ai say`, against the
real ai stacklet and a throwaway instance. A real HTTP server stands in
for the speech server, and an `afplay` on PATH that only records what it
was given stands in for the Mac's player, so no test makes a sound.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from stack import Stack

REPO_ROOT = Path(__file__).resolve().parents[3]

AUDIO = b"ID3 not really an mp3"


def _instance(tmp_path, language):
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\n\n'
        f'[ai]\nlanguage = "{language}"\n')
    (tmp_path / ".stack").mkdir()
    return Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path)


@pytest.fixture
def instance(tmp_path):
    return _instance(tmp_path, "en")


@pytest.fixture
def played(tmp_path, monkeypatch):
    """Where the stand-in player copies what it was asked to play."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    out = tmp_path / "played.mp3"
    player = bin_dir / "afplay"
    # The file is the last argument; the arguments before it go to played.args.
    player.write_text(f'#!/bin/sh\nfor a; do last=$a; done\necho "$@" > "{out}.args"\n'
                      f'cp "$last" "{out}"\n')
    player.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return out


@pytest.fixture
def speech_server(httpserver, monkeypatch):
    """An OpenAI-compatible speech server, as openedai-speech answers."""
    httpserver.expect_request(
        "/v1/audio/speech", method="POST").respond_with_data(
            AUDIO, content_type="audio/mpeg")
    monkeypatch.setenv("STACK_SPEECH_URL", httpserver.url_for("/"))
    return httpserver


def _say(instance, *args):
    return instance.run_cli_command("ai", "say", list(args))


def _requests(server):
    return [request.get_json() for request, _ in server.log]


class TestSpeaking:

    def test_the_text_is_spoken_in_the_default_voice(
            self, instance, speech_server, played):
        """English, like `ai hello`, speaks as alloy."""
        result = _say(instance, "Dinner is ready")

        assert "error" not in result, result
        [request] = _requests(speech_server)
        assert request["input"] == "Dinner is ready"
        assert request["voice"] == "alloy"
        assert played.read_bytes() == AUDIO

    def test_german_gets_the_german_voice(self, tmp_path, speech_server, played):
        """The default voice follows `[ai] language`, the same rule as
        `ai hello`: alloy reads German with English pronunciation."""
        result = _say(_instance(tmp_path, "de"), "Das Essen ist fertig")

        assert result["voice"] == "onyx"
        assert _requests(speech_server)[0]["voice"] == "onyx"

    def test_a_chosen_voice_speaks_it(self, instance, speech_server, played):
        result = _say(instance, "Dinner is ready", "--voice", "nova")

        assert "error" not in result, result
        assert _requests(speech_server)[0]["voice"] == "nova"
        assert played.read_bytes() == AUDIO

    def test_the_result_says_which_voice_spoke(self, instance, speech_server, played):
        """What `--json` prints: an agent or a script can tell what it got."""
        result = _say(instance, "Dinner is ready", "--voice", "echo")

        assert result["voice"] == "echo"

    def test_words_without_quotes_are_one_sentence(
            self, instance, speech_server, played):
        _say(instance, "Dinner", "is", "ready")

        assert _requests(speech_server)[0]["input"] == "Dinner is ready"


class TestNothingIsPlayed:

    def test_an_unknown_voice_names_the_voices_there_are(
            self, instance, speech_server, played):
        """The speech server knows the voices in the map it loads. A typo
        is refused before anything is sent."""
        result = _say(instance, "Dinner is ready", "--voice", "bart")

        assert "bart" in result["error"]
        for voice in ("alloy", "echo", "nova", "onyx", "fable", "shimmer"):
            assert voice in result["error"]
        assert _requests(speech_server) == []
        assert not played.exists()

    def test_no_text_says_how_to_use_it(self, instance, speech_server, played):
        result = _say(instance)

        assert "stack ai say" in result["error"]
        assert _requests(speech_server) == []
        assert not played.exists()

    def test_a_blank_text_is_no_text(self, instance, speech_server, played):
        result = _say(instance, "   ")

        assert "stack ai say" in result["error"]
        assert not played.exists()

    def test_no_speech_server_says_to_start_the_ai_stacklet(
            self, instance, monkeypatch, played):
        monkeypatch.setenv("STACK_SPEECH_URL", "http://127.0.0.1:9")

        result = _say(instance, "Dinner is ready")

        assert "stack up ai" in result["error"]
        assert not played.exists()


def test_say_speaks_at_the_welcome_messages_pace(instance, speech_server, played):
    """The welcome message's voice and pace were chosen by ear, and 1.0
    sounds rushed. Every command that speaks uses them, so `say` sounds
    like `hello`."""
    _say(instance, "Dinner is ready")
    instance.run_cli_command("ai", "hello", [])

    say, hello = _requests(speech_server)
    assert say["speed"] == hello["speed"] == 0.8
    assert say["voice"] == hello["voice"] == "alloy"


class TestHowLoud:
    """Played at a share of the Mac's own output volume, never above it, and
    the system volume is never changed: a muted Mac stays silent (#241)."""

    def test_the_admins_volume_is_a_share_of_the_macs(self, tmp_path, speech_server, played):
        instance = _instance(tmp_path, "en")
        with open(tmp_path / "stack.toml", "a") as f:
            f.write("speech_volume = 50\n")

        _say(instance, "Dinner is ready")

        assert Path(f"{played}.args").read_text().split()[:2] == ["-v", "0.5"]

    def test_a_volume_for_one_sentence_wins(self, instance, speech_server, played):
        _say(instance, "Dinner is ready", "--volume", "30")

        assert Path(f"{played}.args").read_text().split()[:2] == ["-v", "0.3"]

    def test_never_louder_than_the_mac(self, instance, speech_server, played):
        _say(instance, "Dinner is ready", "--volume", "250")

        assert Path(f"{played}.args").read_text().split()[:2] == ["-v", "1.0"]


def test_a_speech_server_elsewhere_speaks_it(tmp_path, httpserver, played, monkeypatch):
    """`[ai] speech_url` names a speech server the stack does not run, as
    `whisper_url` does for hearing."""
    monkeypatch.delenv("STACK_SPEECH_URL", raising=False)
    httpserver.expect_request("/v1/audio/speech", method="POST").respond_with_data(
        AUDIO, content_type="audio/mpeg")
    instance = _instance(tmp_path, "en")
    with open(tmp_path / "stack.toml", "a") as f:
        f.write(f'speech_url = "{httpserver.url_for("/")}"\n')

    result = _say(instance, "Dinner is ready")

    assert "error" not in result, result
    assert played.read_bytes() == AUDIO
