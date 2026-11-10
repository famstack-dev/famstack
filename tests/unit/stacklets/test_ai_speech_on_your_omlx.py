"""Voice messages go to an oMLX the admin runs, without whisper.cpp.

With speech on, the AI's first start builds whisper.cpp for a dedicated
speech server. When the stack's AI server is an oMLX it does not manage,
such as the oMLX app, that oMLX transcribes as well with
`mlx-community/whisper-large-v3-turbo-asr-fp16`. The first start then
offers that model with the chat model's rules (on this Mac default yes,
on another machine default no), checks one transcription, sets
`[ai] whisper_model` and builds nothing. A no, a failed download or a
failed transcription builds whisper.cpp as before.

The `on_install` hook runs as the first `stack up ai` runs it, against
the real ai stacklet and a throwaway instance. A real HTTP server stands
in for oMLX (`omlx_stand_in.py`). A `brew`, `git`, `cmake` and `curl` on
PATH record every command, so a test sees whether the whisper.cpp build
started. They build nothing, so a build that starts ends in "Build
failed": the hook's result is not what these tests read.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from stack import Stack
from stack.hooks import HookResolver, build_hook_ctx
from stack.output import CollectorOutput
from stacklets.ai.speech_on_omlx import SPEECH_MODEL, speech_on_omlx
from tests.unit.stacklets.omlx_stand_in import KEY, OMLX

REPO_ROOT = Path(__file__).resolve().parents[3]
AI = REPO_ROOT / "stacklets" / "ai"
SPEECH_ID = "whisper-large-v3-turbo-asr-fp16"


@pytest.fixture(autouse=True)
def voice_on(monkeypatch):
    monkeypatch.delenv("STACK_AI_NO_VOICE", raising=False)


@pytest.fixture(autouse=True)
def host_commands(tmp_path, monkeypatch):
    """Build tools that record every command line they are given. HOME
    is the test's, so nothing reaches this Mac's LaunchAgents."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "host-calls"
    for name in ("brew", "git", "cmake", "curl"):
        script = bin_dir / name
        script.write_text(f'#!/bin/sh\necho "{name} $@" >> "{calls}"\n')
        script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return calls


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


def _instance(tmp_path, server, *, provider="external", extra="") -> Stack:
    """An instance whose AI server is `server`, as `stack ai switch` or
    the setup's "Use it?" writes it."""
    url = server.url_for("/").rstrip("/")
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\nlanguage = "en"\n\n'
        f'[ai]\nprovider = "{provider}"\nopenai_url = "{url}/v1"\nopenai_key = "{KEY}"\n{extra}')
    (tmp_path / ".stack").mkdir()
    return Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path, output=CollectorOutput())


def _ctx(stack):
    return build_hook_ctx("ai", env=stack.env("ai"), step_fn=stack.output.step, stack=stack)


def _first_install(stack) -> None:
    """The `on_install` hook of the first `stack up ai`."""
    HookResolver(AI).run("on_install", _ctx(stack))


def _ai(tmp_path) -> dict:
    return tomllib.loads((tmp_path / "stack.toml").read_text())["ai"]


def _built_whisper(calls: Path) -> bool:
    return calls.exists() and "git clone" in calls.read_text()


# ── An oMLX on this Mac ──────────────────────────────────────────────────

def test_the_omlx_app_gets_the_speech_model_and_no_whisper_is_built(
        tmp_path, httpserver, host_commands, answers):
    omlx = OMLX(httpserver)
    prompts = answers("")
    stack = _instance(tmp_path, httpserver)

    _first_install(stack)

    assert omlx.requested == [SPEECH_MODEL]
    assert omlx.transcribed == [SPEECH_ID]
    assert _ai(tmp_path)["whisper_model"] == SPEECH_ID
    assert "whisper_url" not in _ai(tmp_path)
    assert not _built_whisper(host_commands)
    [question] = prompts
    assert "Y/n" in question


def test_voice_messages_go_to_the_omlx(tmp_path, httpserver, answers):
    """No `whisper_url` of its own: the bot runner sends voice messages to
    the AI server, with the model it named."""
    OMLX(httpserver)
    answers("")
    stack = _instance(tmp_path, httpserver)

    _first_install(stack)

    core = Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path,
                 output=CollectorOutput()).env("core")
    assert core["WHISPER_URL"] == f"{core['OPENAI_URL']}/audio/transcriptions"
    assert core["WHISPER_KEY"] == KEY
    assert core["WHISPER_MODEL"] == SPEECH_ID


def test_a_model_already_there_is_not_downloaded_and_nothing_is_asked(
        tmp_path, httpserver, host_commands, answers):
    omlx = OMLX(httpserver, downloaded=[SPEECH_ID])
    prompts = answers("n")
    stack = _instance(tmp_path, httpserver)

    _first_install(stack)

    assert omlx.requested == [] and prompts == []
    assert _ai(tmp_path)["whisper_model"] == SPEECH_ID
    assert not _built_whisper(host_commands)


# ── When the oMLX cannot transcribe ──────────────────────────────────────

def test_a_no_builds_whisper_as_before(tmp_path, httpserver, host_commands, answers):
    omlx = OMLX(httpserver)
    answers("n")
    stack = _instance(tmp_path, httpserver)

    _first_install(stack)

    assert omlx.requested == []
    assert "whisper_model" not in _ai(tmp_path)
    assert _built_whisper(host_commands)


def test_a_failed_download_says_so_and_builds_whisper(tmp_path, httpserver, host_commands, answers):
    OMLX(httpserver, fetches=False)
    answers("")
    stack = _instance(tmp_path, httpserver)

    _first_install(stack)

    assert any(SPEECH_MODEL in w for w in stack.output.warnings)
    assert "whisper_model" not in _ai(tmp_path)
    assert _built_whisper(host_commands)


def test_a_failed_transcription_says_so_and_builds_whisper(
        tmp_path, httpserver, host_commands, answers):
    OMLX(httpserver, transcribes=False)
    answers("")
    stack = _instance(tmp_path, httpserver)

    _first_install(stack)

    assert any(SPEECH_ID in w for w in stack.output.warnings)
    assert "whisper_model" not in _ai(tmp_path)
    assert _built_whisper(host_commands)


# ── An oMLX on another machine ───────────────────────────────────────────

def test_one_elsewhere_says_where_and_no_is_the_default(
        tmp_path, httpserver, host_commands, answers, elsewhere):
    omlx = OMLX(httpserver)
    prompts = answers("")
    stack = _instance(tmp_path, httpserver)

    _first_install(stack)

    [question] = prompts
    assert "onto the AI server at 127.0.0.1" in question and "y/N" in question
    assert omlx.requested == []
    assert _built_whisper(host_commands)


def test_one_elsewhere_gets_it_after_a_yes(tmp_path, httpserver, host_commands, answers, elsewhere):
    omlx = OMLX(httpserver)
    answers("y")
    stack = _instance(tmp_path, httpserver)

    _first_install(stack)

    assert omlx.requested == [SPEECH_MODEL]
    assert not _built_whisper(host_commands)


# ── Where whisper.cpp stays ──────────────────────────────────────────────

def test_the_stacks_own_omlx_is_left_to_whisper(tmp_path, httpserver, answers):
    """The engine the stack runs keeps its whisper.cpp: this card moves
    only the route with an oMLX the admin runs."""
    omlx = OMLX(httpserver)
    prompts = answers("y")
    stack = _instance(tmp_path, httpserver, provider="managed")

    assert speech_on_omlx(_ctx(stack)) is False
    assert omlx.requested == [] and prompts == []
    assert httpserver.log == []


def test_a_server_that_is_not_an_omlx_builds_whisper(tmp_path, httpserver, host_commands, answers):
    httpserver.expect_request("/v1/models").respond_with_json({"data": [{"id": "gpt-4.1-mini"}]})
    prompts = answers("y")
    stack = _instance(tmp_path, httpserver)

    _first_install(stack)

    assert prompts == []
    assert _built_whisper(host_commands)


def test_a_speech_server_the_admin_named_is_kept(tmp_path, httpserver, host_commands, answers):
    """`stack ai switch <url> --whisper <url>` chose where voice goes."""
    omlx = OMLX(httpserver)
    prompts = answers("y")
    stack = _instance(tmp_path, httpserver, extra='whisper_url = "http://stt.example.test/v1"\n')

    assert speech_on_omlx(_ctx(stack)) is False
    assert omlx.requested == [] and prompts == []


def test_speech_off_changes_nothing(tmp_path, httpserver, host_commands, answers):
    omlx = OMLX(httpserver)
    prompts = answers("y")
    stack = _instance(tmp_path, httpserver, extra="speech = false\n")

    _first_install(stack)

    assert omlx.requested == [] and prompts == []
    assert "whisper_model" not in _ai(tmp_path)
    assert not _built_whisper(host_commands)
