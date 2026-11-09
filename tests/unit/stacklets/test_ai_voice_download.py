"""`stack up ai` downloads the stack's speech models through oMLX.

The stack speaks through oMLX: Kokoro for English and Qwen3-TTS 0.6B
Base for German. oMLX answers a speech request for a
model it has not downloaded with 404, so `stack up ai` fetches the house
voice's model with oMLX's own downloader, the one language models come
through. Only the house voice's: another language's is the admin's to
add with `stack ai download`, and until then its texts are read in the
house voice. Nothing runs in a container for it.

Only the engine the stack runs gets them. A server the stack does not
manage, the AI server after `stack ai switch <url>` or one `[ai]
speech_url` names, is its admin's to fill. A download that fails is a
warning that names the model, never a failed `up`: chat and transcription
work without it.

The hooks run as `stack up ai` runs them, against the real ai stacklet
and a throwaway instance. A real HTTP server stands in for oMLX's admin
API, answering as oMLX 0.6.4 does: a login with the server's key, the
downloaded models by folder name, a download as a task that completes or
fails, its version in the API description. A `docker` and a `brew` on
PATH record any command, so a test sees that none was run.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from stack import Stack
from stack.hooks import HookResolver, build_hook_ctx
from stack.output import CollectorOutput
from stacklets.ai.voices import download_house_voice

REPO_ROOT = Path(__file__).resolve().parents[3]
AI = REPO_ROOT / "stacklets" / "ai"

from tests.unit.stacklets.omlx_stand_in import KEY, OMLX

ENGLISH = "mlx-community/Kokoro-82M-bf16"
GERMAN = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"


@pytest.fixture(autouse=True)
def voice_on(monkeypatch):
    monkeypatch.delenv("STACK_AI_NO_VOICE", raising=False)


@pytest.fixture(autouse=True)
def host_commands(tmp_path, monkeypatch):
    """A `docker` and a `brew` that record every command line they are given."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "host-calls"
    for name in ("docker", "brew"):
        script = bin_dir / name
        script.write_text(f'#!/bin/sh\necho "{name} $@" >> "{calls}"\n')
        script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return calls


def _instance(tmp_path, server, *, language="en", provider="managed", extra=""):
    """An instance whose AI server is `server`, as `stack up ai` sets it."""
    url = server.url_for("/").rstrip("/")
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\nlanguage = "{language}"\n\n'
        f'[ai]\nlanguage = "{language}"\nprovider = "{provider}"\n'
        f'openai_url = "{url}/v1"\nopenai_key = "{KEY}"\n{extra}')
    (tmp_path / ".stack").mkdir()
    return Stack(REPO_ROOT, tmp_path / "data", instance_dir=tmp_path,
                 output=CollectorOutput())


def _run(stack, hook: str) -> bool:
    """One hook of `stack up ai`, the way the CLI runs it."""
    ctx = build_hook_ctx("ai", env=stack.env("ai"), step_fn=stack.output.step, stack=stack)
    return HookResolver(AI).run(hook, ctx)


def _start(stack) -> bool:
    return _run(stack, "on_start_ready")


def _ctx(stack):
    """The context a hook of `stack up ai` gets."""
    return build_hook_ctx("ai", env=stack.env("ai"), step_fn=stack.output.step, stack=stack)


@pytest.fixture
def elsewhere(monkeypatch):
    """The stand-in plays a server on another machine. The unit lane
    reaches only this Mac, so the stand-in's address is said not to be
    this Mac's. Patched in the framework's module, which every loader of
    the AI stacklet shares, not in a copy of the stacklet's own."""
    monkeypatch.setattr("stack.ai.probe.on_this_mac", lambda _url: False)


@pytest.fixture
def terminal(monkeypatch):
    """A terminal that answers each question with `answer`; returns the
    questions asked."""
    import io
    import sys

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    def answering(answer: str) -> list[str]:
        prompts: list[str] = []
        monkeypatch.setattr(sys, "stdin", Terminal())
        monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or answer)
        return prompts
    return answering


def _commands(calls: Path) -> list[str]:
    return calls.read_text().splitlines() if calls.exists() else []


class TestAFreshMac:

    @pytest.mark.parametrize("language, model", [("en", ENGLISH), ("de", GERMAN),
                                                 ("fr", ENGLISH)])
    def test_only_the_house_voices_model_is_downloaded_through_omlx(
            self, tmp_path, httpserver, host_commands, language, model):
        """Only the model the house language uses is downloaded. A
        language without a voice of its own is read by the English one."""
        omlx = OMLX(httpserver)
        stack = _instance(tmp_path, httpserver, language=language)

        assert _start(stack)

        assert omlx.requested == [model]
        assert _commands(host_commands) == []
        assert stack.output.warnings == []

    def test_a_second_start_downloads_nothing(self, tmp_path, httpserver):
        omlx = OMLX(httpserver)
        stack = _instance(tmp_path, httpserver)
        _start(stack)

        _start(stack)

        assert omlx.requested == [ENGLISH]

    def test_models_already_there_are_not_downloaded_again(self, tmp_path, httpserver):
        """oMLX lists a downloaded model by its folder name."""
        omlx = OMLX(httpserver, downloaded=["Qwen3.5-9B-MLX-8bit", "Kokoro-82M-bf16"])

        assert _start(_instance(tmp_path, httpserver))

        assert omlx.requested == []

    def test_an_instance_with_both_models_keeps_them(self, tmp_path, httpserver):
        """Before 2026-10-27 every language's model was downloaded. The
        other one stays, and a text in its language still uses it."""
        OMLX(httpserver, downloaded=["Kokoro-82M-bf16", "Qwen3-TTS-12Hz-0.6B-Base-8bit"])

        assert _start(_instance(tmp_path, httpserver, language="de"))

        assert {request.method for request, _ in httpserver.log} <= {"GET", "POST"}


class TestVoicesNamedInStackToml:
    """`[ai.voices.<lang>]` replaces a language's voice, so the engine
    gets what the voices in force need and nothing else."""

    GERMAN_1_7B = ('\n[ai.voices.de]\nmodel = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"\n'
                   'clip = "stacky-de"\n')

    def test_a_named_model_is_downloaded_instead_of_the_one_it_replaces(
            self, tmp_path, httpserver):
        """A model written as its Hugging Face repo is downloaded from there."""
        omlx = OMLX(httpserver)
        stack = _instance(tmp_path, httpserver, language="de", extra=self.GERMAN_1_7B)

        assert _start(stack)

        assert omlx.requested == ["mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"]

    def test_another_languages_entry_is_not_downloaded(self, tmp_path, httpserver):
        """Only the house language's voice counts, named or the stack's own."""
        omlx = OMLX(httpserver)
        stack = _instance(tmp_path, httpserver, language="en", extra=self.GERMAN_1_7B)

        assert _start(stack)

        assert omlx.requested == [ENGLISH]

    def test_a_model_without_a_known_source_is_not_tried(self, tmp_path, httpserver):
        omlx = OMLX(httpserver)
        stack = _instance(tmp_path, httpserver,
                          extra='\n[ai.voices.en]\nmodel = "tts-1"\nvoice = "alloy"\n')

        assert _start(stack)

        assert omlx.requested == []
        assert stack.output.warnings == []

    def test_an_entry_the_stack_cannot_use_is_a_warning_that_names_it(
            self, tmp_path, httpserver):
        omlx = OMLX(httpserver)
        stack = _instance(tmp_path, httpserver, extra='\n[ai.voices.en]\nvoice = "alloy"\n')

        assert _start(stack)

        [warning] = stack.output.warnings
        assert "[ai.voices.en]" in warning
        assert omlx.requested == []


class TestAFailedDownloadDoesNotStopTheStart:

    def test_the_admin_is_told_which_model_is_missing(self, tmp_path, httpserver):
        """Chat and transcription work without it, so the start goes on,
        and the warning says what is missing and why."""
        OMLX(httpserver, fetches=False)
        stack = _instance(tmp_path, httpserver)

        assert _start(stack)

        [warning] = stack.output.warnings
        assert ENGLISH in warning and "huggingface.co" in warning
        assert stack.output.errors == []

    def test_the_next_start_tries_again(self, tmp_path, httpserver):
        omlx = OMLX(httpserver, fetches=False)
        stack = _instance(tmp_path, httpserver)
        _start(stack)

        _start(stack)

        assert omlx.requested == [ENGLISH, ENGLISH]

    def test_a_refused_key_is_a_warning_that_says_so(self, tmp_path, httpserver):
        """oMLX's admin login takes the server's key; after a key change in
        oMLX alone, the stack cannot check its models and says why."""
        omlx = OMLX(httpserver)
        stack = _instance(tmp_path, httpserver)
        stack._set_cfg("ai", "openai_key", "an-old-key")

        assert _start(stack)

        [warning] = stack.output.warnings
        assert "openai_key" in warning
        assert omlx.requested == []


class TestSpeechTheStackDoesNotRun:

    def test_a_speech_server_elsewhere_is_left_alone(self, tmp_path, httpserver):
        omlx = OMLX(httpserver)
        stack = _instance(tmp_path, httpserver,
                          extra='speech_url = "https://tts.example.test"\n')

        assert _start(stack)

        assert omlx.requested == []
        assert stack.output.warnings == []

    def test_an_ai_server_that_is_no_omlx_is_left_alone(self, tmp_path, httpserver):
        """A server without oMLX's admin API is its admin's to fill."""
        httpserver.expect_request("/v1/models").respond_with_json({"data": []})

        assert _start(_instance(tmp_path, httpserver, provider="external"))

        assert [r.path for r, _ in httpserver.log if r.method == "POST"] == []

    def test_no_voice_downloads_nothing(self, tmp_path, httpserver, monkeypatch):
        """`stack up ai --no-voice` is for working on the language model
        alone, without the voice services or their downloads."""
        monkeypatch.setenv("STACK_AI_NO_VOICE", "1")
        omlx = OMLX(httpserver)
        stack = _instance(tmp_path, httpserver)

        assert _start(stack)

        assert omlx.requested == []
        assert stack.output.warnings == []


class TestAnOMLXTheAdminRuns:
    """An oMLX the stack does not manage, such as the oMLX app, gets the
    house voice too: on this Mac as the stack's own engine does, on
    another machine only on the first start and after a yes."""

    def test_one_on_this_mac_gets_the_house_voice(self, tmp_path, httpserver):
        omlx = OMLX(httpserver)

        assert _start(_instance(tmp_path, httpserver, provider="external"))

        assert omlx.requested == [ENGLISH]

    def test_one_elsewhere_is_asked_about_first_with_no_as_the_default(
            self, tmp_path, httpserver, elsewhere, terminal):
        omlx = OMLX(httpserver)
        stack = _instance(tmp_path, httpserver, provider="external")
        prompts = terminal("")

        download_house_voice(_ctx(stack), ask=True)

        [question] = prompts
        assert "This downloads 5.8 GB onto the AI server at 127.0.0.1. Download it there?" in question
        assert "y/N" in question
        assert omlx.requested == []

    def test_one_elsewhere_gets_it_after_a_yes(self, tmp_path, httpserver, elsewhere, terminal):
        omlx = OMLX(httpserver)
        terminal("y")

        download_house_voice(_ctx(_instance(tmp_path, httpserver, provider="external")), ask=True)

        assert omlx.requested == [ENGLISH]

    def test_one_elsewhere_is_left_alone_on_a_later_start(self, tmp_path, httpserver, elsewhere):
        """A no on the first start is not asked again on every start."""
        omlx = OMLX(httpserver)

        assert _start(_instance(tmp_path, httpserver, provider="external"))

        assert omlx.requested == []
        assert [r.path for r, _ in httpserver.log if r.method == "POST"] == []


class TestAnOMLXTooOldToSpeak:
    """Speech came to oMLX in 0.3.0, a cloned voice in 0.3.5, and
    Homebrew's Kokoro works since 0.5.0 (oMLX's release notes, checked
    2026-10-27). An older engine the stack runs is named with the command
    that updates it; updating is the admin's, so the stack runs no brew."""

    def test_the_admin_is_told_how_to_update_it(self, tmp_path, httpserver, host_commands):
        omlx = OMLX(httpserver, version="0.4.2")
        stack = _instance(tmp_path, httpserver)

        assert _start(stack)

        [warning] = stack.output.warnings
        assert "0.5.0" in warning and "0.4.2" in warning
        assert "brew upgrade omlx" in warning and "stack restart ai" in warning
        assert omlx.requested == [ENGLISH]
        assert _commands(host_commands) == []

    @pytest.mark.parametrize("elsewhere", [{"provider": "external"},
                                           {"extra": 'speech_url = "https://tts.example.test"\n'}])
    def test_a_server_the_stack_does_not_run_is_not_judged(
            self, tmp_path, httpserver, elsewhere):
        """After `stack ai switch <url>`, or with `[ai] speech_url`."""
        OMLX(httpserver, version="0.4.2")
        stack = _instance(tmp_path, httpserver, **elsewhere)

        assert _start(stack)

        assert stack.output.warnings == []


def test_the_first_install_fetches_the_models_before_the_greeting(tmp_path, httpserver):
    """On the first `stack up ai` the greeting plays in the install step,
    before the ready step runs, so the install step fetches the models
    first. The Mac would otherwise greet with an error."""
    omlx = OMLX(httpserver)
    stack = _instance(tmp_path, httpserver, language="de")

    _run(stack, "on_install_success")

    assert omlx.requested == [GERMAN]
    _start(stack)
    assert omlx.requested == [GERMAN]
