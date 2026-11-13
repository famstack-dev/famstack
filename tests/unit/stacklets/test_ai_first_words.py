"""The AI's first words: `stack ai hello` and `stack ai intro`.

In the terminal, `hello` plays the welcome on the Mac's speakers and
`intro` prints the language model's introduction and speaks it. For the
setup page, `--audio` plays nothing and answers with the words and the
audio, which the page plays in the browser: the welcome as `hello` says
it, the introduction in the model's own words, both in the stack's own
voice.

Run as `./stack ai hello` runs it, against the real ai stacklet and a
throwaway instance whose `[ai]` names a stand-in AI server on localhost:
its speech endpoint answers with fixed bytes and its chat endpoint with
a fixed introduction. An `afplay` on PATH that only records what it was
given stands in for the Mac's player, so no test makes a sound.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from stack import Stack

REPO_ROOT = Path(__file__).resolve().parents[3]

AUDIO = b"ID3 the welcome, not really an mp3"
KEY = "omlx-key"
INTRODUCTION = ("Good evening, Homer. It's Monday, 9 November, 20:58. "
                "I'm famstack's AI, on your Mac with 16 GB, and I'm ready to read your "
                "family's letters.")


@pytest.fixture
def ai_server(httpserver):
    """The AI server: speech answers `AUDIO`, chat answers `INTRODUCTION`."""
    httpserver.expect_request("/v1/audio/speech", method="POST").respond_with_data(
        AUDIO, content_type="audio/mpeg")
    httpserver.expect_request("/v1/chat/completions", method="POST").respond_with_json(
        {"choices": [{"message": {"content": INTRODUCTION}}]})
    return httpserver


@pytest.fixture
def stack(tmp_path, ai_server):
    """The Simpsons' instance, Homer its admin, speaking English."""
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\n\n'
        f'[ai]\nlanguage = "en"\nopenai_url = "{ai_server.url_for("/v1")}"\n'
        f'openai_key = "{KEY}"\n')
    (tmp_path / "users.toml").write_text(
        '[[users]]\nid = "homer"\nname = "Homer Simpson"\nrole = "admin"\n')
    (tmp_path / ".stack").mkdir()
    return Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path)


@pytest.fixture(autouse=True)
def played(tmp_path, monkeypatch):
    """What the stand-in player was asked to play, copied here. Every test
    gets it, so none can reach the Mac's own player."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    out = tmp_path / "played.mp3"
    player = bin_dir / "afplay"
    player.write_text(f'#!/bin/sh\nfor a; do last=$a; done\ncp "$last" "{out}"\n')
    player.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return out


def spoken_texts(server) -> list[str]:
    """The texts the speech server was asked to speak, in order."""
    return [json.loads(request.data)["input"] for request, _ in server.log
            if request.path == "/v1/audio/speech"]


# ── For the page: --audio ────────────────────────────────────────────────

def test_the_welcome_comes_back_as_audio_and_plays_nothing(stack, ai_server, played):
    """Given speech runs, when the page asks for the welcome, then it gets
    the speech server's audio and the words it spoke, and the Mac's
    speakers stay silent."""
    answer = stack.run_cli_command("ai", "hello", ["--audio"])

    assert base64.b64decode(answer["audio"]) == AUDIO
    assert spoken_texts(ai_server) == [answer["text"]]
    assert "Homer" in answer["text"]
    assert not played.exists()


def test_the_introduction_comes_back_in_the_models_words(stack, ai_server, played):
    """The introduction is the language model's answer, spoken as it is,
    so the page can show the words it plays."""
    answer = stack.run_cli_command("ai", "intro", ["--audio"])

    assert answer["text"] == INTRODUCTION
    assert spoken_texts(ai_server) == [INTRODUCTION]
    assert base64.b64decode(answer["audio"]) == AUDIO
    assert not played.exists()


def test_the_model_learns_who_the_admin_is_and_the_time(stack, ai_server):
    stack.run_cli_command("ai", "intro", ["--audio"])

    prompt = next(json.loads(r.data)["messages"][0]["content"] for r, _ in ai_server.log
                  if r.path == "/v1/chat/completions")
    assert "Homer" in prompt
    assert "None" not in prompt


def test_without_speech_the_page_gets_an_error_and_no_audio(tmp_path, httpserver):
    """A speech server that refuses ends in an error, never in silent audio."""
    httpserver.expect_request("/v1/audio/speech").respond_with_data("no", status=500)
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\n\n'
        f'[ai]\nopenai_url = "{httpserver.url_for("/v1")}"\n')
    stack = Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path)

    answer = stack.run_cli_command("ai", "hello", ["--audio"])

    assert "error" in answer and "audio" not in answer


# ── In the terminal: unchanged ───────────────────────────────────────────

def test_hello_plays_the_welcome_on_the_mac(stack, ai_server, played):
    answer = stack.run_cli_command("ai", "hello", [])

    assert answer == {"ok": True}
    assert played.read_bytes() == AUDIO
    assert spoken_texts(ai_server)[0].startswith("Hello Homer. Welcome to famstack.")


def test_intro_speaks_the_models_words_on_the_mac(stack, ai_server, played):
    answer = stack.run_cli_command("ai", "intro", [])

    assert answer == {"ok": True}
    assert spoken_texts(ai_server) == [INTRODUCTION]
    assert played.read_bytes() == AUDIO


# ── With speech off ──────────────────────────────────────────────────────

SPEECH_WORDS = ("speak", "speech", "voice", "hear", "listen")


@pytest.fixture
def speech_off(stack):
    """The Simpsons' instance on a small Mac: `[ai] speech = false`."""
    toml = stack.instance_dir / "stack.toml"
    toml.write_text(toml.read_text() + "speech = false\n")
    return stack


def test_with_speech_off_the_first_start_offers_no_voice_demo(
        speech_off, ai_server, played, monkeypatch, capsys):
    """Given speech off, when the AI's first start ends in a terminal, then
    it asks nothing about hearing the Mac speak and plays nothing. The
    backend check that follows is stood in: it is not what this pins."""
    from stack import prompt
    from stack.hooks import HookResolver, build_hook_ctx
    from stacklets.ai import backend

    asked = []
    monkeypatch.setattr(prompt, "confirm", lambda q, **_: asked.append(q) or True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(backend, "ensure_backend", lambda *_a, **_k: {"error": "stood in"})
    ctx = build_hook_ctx("ai", step_fn=lambda _msg: None, stack=speech_off)

    assert HookResolver(REPO_ROOT / "stacklets" / "ai").run("on_install_success", ctx)

    printed = capsys.readouterr().out.lower()
    assert asked == []
    assert not any(word in printed for word in SPEECH_WORDS), printed
    assert spoken_texts(ai_server) == []
    assert not played.exists()


def test_with_speech_off_the_intro_prints_the_words_and_speaks_nothing(
        speech_off, ai_server, played, capsys):
    answer = speech_off.run_cli_command("ai", "intro", [])

    assert answer == {"ok": True}
    assert INTRODUCTION in capsys.readouterr().out
    assert spoken_texts(ai_server) == []
    assert not played.exists()


def test_with_speech_off_the_ais_summary_names_no_speech(speech_off, docker_with_no_containers):
    """The line under "AI is running" after `stack up ai`, and in `stack
    list`, names only what runs."""
    ai = next(s for s in speech_off.list()["stacklets"] if s["id"] == "ai")

    assert ai["description"]
    assert not any(word in ai["description"].lower() for word in SPEECH_WORDS), ai


def test_in_german_the_model_is_told_to_say_du(stack, ai_server):
    """The stack says du to the family, and the AI's introduction does too:
    without it a model answered with "Ihnen"."""
    toml = stack.instance_dir / "stack.toml"
    toml.write_text(toml.read_text().replace('language = "en"', 'language = "de"'))

    stack.run_cli_command("ai", "intro", ["--audio"])

    prompt = next(json.loads(r.data)["messages"][0]["content"] for r, _ in ai_server.log
                  if r.path == "/v1/chat/completions")
    assert "Duze" in prompt
