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
fails. A `docker` on PATH records any command, so a test sees that none
was run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from werkzeug import Response

from stack import Stack
from stack.hooks import HookResolver, build_hook_ctx
from stack.output import CollectorOutput

REPO_ROOT = Path(__file__).resolve().parents[3]
AI = REPO_ROOT / "stacklets" / "ai"

KEY = "omlx-key"
ENGLISH = "mlx-community/Kokoro-82M-bf16"
GERMAN = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"
SESSION = "omlx_admin_session"


class OMLX:
    """oMLX's admin API: what the stack logs in to and downloads through.

    `downloaded` are the models on disk, by folder name; a download adds
    its repo's folder name when it `fetches`, and fails as one does
    without Hugging Face otherwise. `requested` are the repos the stack
    asked for, in order.
    """

    def __init__(self, server, downloaded=(), fetches=True):
        self.downloaded = list(downloaded)
        self.fetches = fetches
        self.requested: list[str] = []
        self._tasks: list[dict] = []
        server.expect_request("/admin/api/login", method="POST").respond_with_handler(self._login)
        server.expect_request("/admin/api/models", method="GET").respond_with_handler(
            self._admin(lambda _: {"models": [{"id": m} for m in self.downloaded]}))
        server.expect_request("/admin/api/hf/download", method="POST").respond_with_handler(
            self._admin(self._download))
        server.expect_request("/admin/api/hf/tasks", method="GET").respond_with_handler(
            self._admin(lambda _: {"tasks": self._tasks}))
        # The LLM check the first install runs before its greeting.
        server.expect_request("/v1/models").respond_with_json({"object": "list", "data": []})

    def _login(self, request):
        if request.get_json().get("api_key") != KEY:
            return Response(json.dumps({"detail": "Invalid API key"}), status=401,
                            content_type="application/json")
        response = Response(json.dumps({"success": True}), content_type="application/json")
        response.set_cookie(SESSION, "admin")
        return response

    def _admin(self, answer):
        """An admin route: 401 without the session the login set."""
        def handler(request):
            if request.cookies.get(SESSION) != "admin":
                return Response(json.dumps({"detail": "Not authenticated"}), status=401,
                                content_type="application/json")
            return Response(json.dumps(answer(request)), content_type="application/json")
        return handler

    def _download(self, request):
        repo = request.get_json()["repo_id"]
        self.requested.append(repo)
        task = {"task_id": f"task-{len(self._tasks)}", "repo_id": repo, "progress": 0.0,
                "total_size": 0, "downloaded_size": 0, "error": ""}
        self._tasks.append(task)
        answer = {"success": True, "task": {**task, "status": "pending"}}
        if self.fetches:
            task["status"], task["progress"] = "completed", 100.0
            self.downloaded.append(repo.rsplit("/", 1)[-1])
        else:
            task["status"] = "failed"
            task["error"] = "Failed to reach huggingface.co"
        return answer


@pytest.fixture(autouse=True)
def voice_on(monkeypatch):
    monkeypatch.delenv("STACK_AI_NO_VOICE", raising=False)


@pytest.fixture(autouse=True)
def docker(tmp_path, monkeypatch):
    """A `docker` that records every command line it is given."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "docker-calls"
    script = bin_dir / "docker"
    script.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\n')
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


def _commands(calls: Path) -> list[str]:
    return calls.read_text().splitlines() if calls.exists() else []


class TestAFreshMac:

    @pytest.mark.parametrize("language, model", [("en", ENGLISH), ("de", GERMAN),
                                                 ("fr", ENGLISH)])
    def test_only_the_house_voices_model_is_downloaded_through_omlx(
            self, tmp_path, httpserver, docker, language, model):
        """Arthur, 2026-10-27: only the model the house language uses. A
        language without a voice of its own is read by the English one."""
        omlx = OMLX(httpserver)
        stack = _instance(tmp_path, httpserver, language=language)

        assert _start(stack)

        assert omlx.requested == [model]
        assert _commands(docker) == []
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

    def test_an_ai_server_elsewhere_is_left_alone(self, tmp_path, httpserver):
        """After `stack ai switch <url>` the AI server is another machine's."""
        omlx = OMLX(httpserver)

        assert _start(_instance(tmp_path, httpserver, provider="external"))

        assert omlx.requested == []

    def test_no_voice_downloads_nothing(self, tmp_path, httpserver, monkeypatch):
        """`stack up ai --no-voice` is for working on the language model
        alone, without the voice services or their downloads."""
        monkeypatch.setenv("STACK_AI_NO_VOICE", "1")
        omlx = OMLX(httpserver)
        stack = _instance(tmp_path, httpserver)

        assert _start(stack)

        assert omlx.requested == []
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
