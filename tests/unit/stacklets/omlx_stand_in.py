"""A stand-in for oMLX's admin API, for the tests of what the stack
downloads onto an oMLX.

A real HTTP server (pytest-httpserver) answers as oMLX 0.6.4 and the
oMLX app 0.7.4 do: a login with the server's key that sets a session
cookie, the downloaded models by folder name, a model's size from
Hugging Face, a download as a task that completes or fails, loading a
model, a transcription with a speech model on disk, a chat completion,
and its version in the API description. As the oMLX app does, it can want
its key for the OpenAI routes (`wants_key`), and hold a download half done
(`holds`). As an oMLX built for a newer macOS does, it can list its
models and fail every completion (`computes=False`).
Its global settings answer and change as oMLX 0.7.4's do: a change is
applied at once and saved, except the fields named in `at_start`, which
are saved only.

    omlx = OMLX(httpserver, downloaded=["Kokoro-82M-bf16"])
    ...                                   # the stack's hook or command runs
    omlx.requested                        # ['mlx-community/Qwen3.5-9B-MLX-8bit']
    omlx.loaded                           # ['Qwen3.5-9B-MLX-8bit']
    omlx.transcribed                      # ['whisper-large-v3-turbo-asr-fp16']
    omlx.completed                        # ['mlx-community/Qwen3.5-9B-MLX-4bit']: one answer
    omlx.settings["scheduler"]            # {'chunked_prefill': True} after a change
    omlx.changes                          # [{'chunked_prefill': True}]: what was sent

    app = OMLX(httpserver, wants_key=True, holds=True)
    ...                                   # a download starts, and stays at half
    app.finish()                          # every held download completes

    broken = OMLX(httpserver, computes=False)
    ...                                   # /v1/models lists, a completion answers 500
                                          # with `BROKEN`, as oMLX's own error

    with hanging() as server:             # a server whose completions never answer
        ...                               # server.url_for("/v1")
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from werkzeug import Response

__all__ = ["KEY", "OMLX", "SIZE", "BROKEN", "hanging"]

KEY = "omlx-key"
SESSION = "omlx_admin_session"
# A model's size as oMLX reports it from Hugging Face.
SIZE = {"size": 6_227_702_579, "size_formatted": "5.8 GB"}

# What a completion answers on an oMLX that cannot compute, as a build for
# a newer macOS fails when it loads its Metal kernels.
BROKEN = "Failed to load the default metallib. library not found"

# The three global settings the stack reads, as the oMLX app 0.7.4 starts
# with them: its default memory guard, prefill in one piece, no idle
# timeout.
APP_SETTINGS = {"memory": {"memory_guard_tier": "balanced", "memory_guard_custom_ceiling_gb": 0.0},
                "scheduler": {"chunked_prefill": False},
                "idle_timeout": {"idle_timeout_seconds": None}}
# Where each field of a settings change sits in the settings (oMLX 0.7.4).
SECTIONS = {"memory_guard_tier": "memory", "memory_guard_custom_ceiling_gb": "memory",
            "chunked_prefill": "scheduler", "idle_timeout_seconds": "idle_timeout"}


class OMLX:
    """oMLX's admin API: what the stack logs in to and downloads through.

    `downloaded` are the models on disk, by folder name; a download adds
    its repo's folder name when it `fetches`, and fails as one does
    without Hugging Face otherwise. `requested` are the repos the stack
    asked for, in order; `loaded` the models it loaded. Every model on
    Hugging Face measures `SIZE`. `transcribed` are the models a
    transcription named; one not on disk, or any when `transcribes` is
    False, answers 404 as oMLX does for a model it cannot load.
    `wants_key` answers `/v1/models` only with the key, as the oMLX app
    does; `holds` keeps a download at half until `finish`. `sizes` gives
    a repo a size in bytes of its own, for a page that shows them side by
    side (`{"mlx-community/whisper-large-v3-turbo-asr-fp16": 1_618_636_172}`).
    `settings` are its global settings in sections (`APP_SETTINGS` by
    default); `changes` the changes sent to them, in order; `at_start` the
    fields it saves without applying them at once. `completed` are the
    models a chat completion named, each answered with `says`; with
    `computes` False each one
    answers 500 with oMLX's error (`BROKEN`), and a test may set it True
    later, as an admin who installed the right build.
    """

    def __init__(self, server, downloaded=(), fetches=True, version="0.6.4", transcribes=True,
                 *, wants_key=False, holds=False, sizes: dict[str, int] | None = None,
                 settings: dict | None = None, at_start=(), computes=True, says="OK"):
        self.downloaded = list(downloaded)
        self.computes = computes
        self.says = says
        self.completed: list[str] = []
        self.fetches = fetches
        self.wants_key = wants_key
        self.holds = holds
        self.transcribes = transcribes
        self.sizes = sizes or {}
        self.requested: list[str] = []
        self.loaded: list[str] = []
        self.transcribed: list[str] = []
        self.settings = json.loads(json.dumps(settings if settings is not None else APP_SETTINGS))
        self.changes: list[dict] = []
        self.at_start = tuple(at_start)
        self._tasks: list[dict] = []
        server.expect_request("/admin/api/login", method="POST").respond_with_handler(self._login)
        server.expect_request("/admin/api/models", method="GET").respond_with_handler(
            self._admin(lambda _: {"models": [{"id": m} for m in self.downloaded]}))
        server.expect_request("/admin/api/hf/download", method="POST").respond_with_handler(
            self._admin(self._download))
        server.expect_request("/admin/api/hf/model-info", method="GET").respond_with_handler(
            self._admin(lambda request: {"repo_id": request.args["repo_id"],
                                         **self._size(request.args["repo_id"])}))
        server.expect_request(re.compile(r"/admin/api/models/[^/]+/load"),
                              method="POST").respond_with_handler(self._admin(self._load))
        server.expect_request("/admin/api/hf/tasks", method="GET").respond_with_handler(
            self._admin(lambda _: {"tasks": self._tasks}))
        server.expect_request("/admin/api/global-settings", method="GET").respond_with_handler(
            self._admin(lambda _: self.settings))
        server.expect_request("/admin/api/global-settings", method="POST").respond_with_handler(
            self._admin(self._change_settings))
        server.expect_request("/v1/audio/transcriptions", method="POST").respond_with_handler(
            self._transcribe)
        # The LLM check the first install runs before its greeting.
        server.expect_request("/v1/models").respond_with_handler(self._models)
        server.expect_request("/v1/chat/completions", method="POST").respond_with_handler(
            self._complete)
        # oMLX is a FastAPI app: its version is in the API description,
        # served without a key.
        server.expect_request("/openapi.json").respond_with_json(
            {"openapi": "3.1.0", "info": {"title": "oMLX API",
                                          "description": "LLM inference, optimized for your Mac",
                                          "version": version}})

    def _login(self, request):
        if request.get_json().get("api_key") != KEY:
            return Response(json.dumps({"detail": "Invalid API key"}), status=401,
                            content_type="application/json")
        response = Response(json.dumps({"success": True}), content_type="application/json")
        response.set_cookie(SESSION, "admin")
        return response

    def finish(self) -> None:
        """Complete every download `holds` kept at half."""
        for task in self._tasks:
            if task["status"] == "downloading":
                task.update(status="completed", progress=100.0,
                            downloaded_size=task["total_size"])
                self.downloaded.append(task["repo_id"].rsplit("/", 1)[-1])

    def _models(self, request):
        if self.wants_key and request.headers.get("Authorization") != f"Bearer {KEY}":
            return Response(json.dumps({"detail": "Invalid API key"}), status=401,
                            content_type="application/json")
        return Response(json.dumps({"object": "list", "data": []}),
                        content_type="application/json")

    def _complete(self, request):
        """The OpenAI chat route: one short answer, or oMLX's 500 when it
        cannot compute."""
        if self.wants_key and request.headers.get("Authorization") != f"Bearer {KEY}":
            return Response(json.dumps({"detail": "Invalid API key"}), status=401,
                            content_type="application/json")
        model = request.get_json().get("model", "")
        if not self.computes:
            return Response(json.dumps({"detail": BROKEN}), status=500,
                            content_type="application/json")
        self.completed.append(model)
        return Response(json.dumps({"object": "chat.completion", "model": model, "choices": [
            {"index": 0, "finish_reason": "length",
             "message": {"role": "assistant", "content": self.says}}]}),
            content_type="application/json")

    def _admin(self, answer):
        """An admin route: 401 without the session the login set."""
        def handler(request):
            if request.cookies.get(SESSION) != "admin":
                return Response(json.dumps({"detail": "Admin authentication required"}),
                                status=401, content_type="application/json")
            return Response(json.dumps(answer(request)), content_type="application/json")
        return handler

    def _download(self, request):
        repo = request.get_json()["repo_id"]
        self.requested.append(repo)
        task = {"task_id": f"task-{len(self._tasks)}", "repo_id": repo, "progress": 0.0,
                "total_size": self._size(repo)["size"], "downloaded_size": 0, "error": ""}
        self._tasks.append(task)
        answer = {"success": True, "task": {**task, "status": "pending"}}
        if self.holds:
            task.update(status="downloading", progress=50.0,
                        downloaded_size=task["total_size"] // 2)
        elif self.fetches:
            task["status"], task["progress"] = "completed", 100.0
            self.downloaded.append(repo.rsplit("/", 1)[-1])
        else:
            task["status"] = "failed"
            task["error"] = "Failed to reach huggingface.co"
        return answer

    def _change_settings(self, request):
        """Save a change into `settings`; list as applied what is not in
        `at_start`."""
        change = request.get_json()
        self.changes.append(change)
        for name, value in change.items():
            self.settings.setdefault(SECTIONS[name], {})[name] = value
        return {"success": True, "message": "Settings saved",
                "runtime_applied": [name for name in change if name not in self.at_start]}

    def _size(self, repo: str) -> dict:
        """A repo's size as oMLX reports it: its own from `sizes`, else `SIZE`."""
        if repo not in self.sizes:
            return SIZE
        return {"size": self.sizes[repo], "size_formatted": f"{self.sizes[repo] / 1024 ** 3:.1f} GB"}

    def _load(self, request):
        self.loaded.append(request.path.split("/")[-2])
        return {"success": True}

    def _transcribe(self, request):
        """The OpenAI transcription route, answered in `verbose_json`'s
        shape for a model on disk."""
        model = request.form.get("model", "")
        if request.headers.get("Authorization") != f"Bearer {KEY}":
            return Response(json.dumps({"detail": "Invalid API key"}), status=401,
                            content_type="application/json")
        if not self.transcribes or model not in self.downloaded or "file" not in request.files:
            return Response(json.dumps({"detail": f"Model '{model}' not found"}), status=404,
                            content_type="application/json")
        self.transcribed.append(model)
        return Response(json.dumps({"text": "", "language": "en", "duration": 1.0, "segments": []}),
                        content_type="application/json")



@contextmanager
def hanging() -> Iterator:
    """A server of its own whose chat completions answer nothing until it
    stops, as an AI that is still loading a large model. It is not the
    shared `httpserver`, because a request still waiting when the test
    ends would be logged into the next test's log there. The waiting
    request is released when the block ends, never by a clock.

        with hanging() as server:
            ask(server.url_for("/v1"))    # times out
    """
    from pytest_httpserver import HTTPServer

    released = threading.Event()

    def wait(_request):
        released.wait()
        return Response("{}", content_type="application/json")

    server = HTTPServer(host="127.0.0.1", port=0)
    server.expect_request("/v1/chat/completions", method="POST").respond_with_handler(wait)
    server.start()
    try:
        yield server
    finally:
        released.set()
        server.clear()
        server.stop()
