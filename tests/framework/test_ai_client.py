"""Unit tests for `stack.ai.client.LLM`.

The client is a thin wrapper around the OpenAI Python SDK with three
responsibilities the SDK can't own: famstack's role->model router,
typed friendly errors, and a vision-capability cache.

We exercise it against `pytest-httpserver` mocking an OpenAI-compatible
endpoint — the same shape oMLX, Ollama, and OpenAI itself speak — so the
tests prove transport-level behaviour, not internal call patterns.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI
from pytest_httpserver import HTTPServer

# The framework lives in lib/.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))

import stack.ai.models  # noqa: E402  — patched module-side, see fixture
from stack.ai.client import (  # noqa: E402
    LLM,
    LLMImage,
    LLMModelNotFoundError,
    LLMTimeoutError,
    LLMUnavailableError,
    ModelCapabilities,
    Transcriber,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def stub_resolver(monkeypatch):
    """Pin `resolve_model` so every role maps to `"test-model"`.

    The router itself is covered by `test_models.py`; here it's noise.
    """
    monkeypatch.setattr(stack.ai.models, "_DEFAULT_MODEL", "test-model")
    monkeypatch.setattr(stack.ai.models, "_MODELS", {})


def _make_llm(httpserver: HTTPServer, *, timeout: float = 5.0,
              capabilities: ModelCapabilities | None = None) -> LLM:
    """Build an LLM pointed at the local httpserver mock.

    `max_retries=0` keeps tests fast and lets a 401/404 surface on the
    first response instead of being eaten by the SDK's auto-retry."""
    client = AsyncOpenAI(
        base_url=httpserver.url_for("/v1"),
        api_key="not-needed",
        max_retries=0,
        timeout=timeout,
    )
    return LLM(client, namespace="test-bot", capabilities=capabilities)


def _completion_payload(content: str, *, model: str = "test-model") -> dict:
    return {
        "id": "cmpl-test",
        "object": "chat.completion",
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# ── complete(): happy path ───────────────────────────────────────────────

class TestComplete:
    async def test_returns_assistant_text(self, httpserver: HTTPServer):
        httpserver.expect_request(
            "/v1/chat/completions", method="POST",
        ).respond_with_json(_completion_payload("hello world"))

        llm = _make_llm(httpserver)
        result = await llm.complete("classifier", "say hi")
        assert result == "hello world"
        await llm.aclose()

    async def test_sends_resolved_model(self, httpserver: HTTPServer, monkeypatch):
        """Role goes through resolve_model — the wire payload uses the
        resolver's answer, not the role string."""
        monkeypatch.setattr(stack.ai.models, "_DEFAULT_MODEL", "qwen3-14b")
        captured: dict = {}

        def handler(request):
            captured["body"] = json.loads(request.get_data().decode())
            from werkzeug.wrappers import Response
            return Response(
                json.dumps(_completion_payload("ok")),
                content_type="application/json",
            )

        httpserver.expect_request(
            "/v1/chat/completions", method="POST",
        ).respond_with_handler(handler)

        llm = _make_llm(httpserver)
        await llm.complete("classifier", "hi")
        assert captured["body"]["model"] == "qwen3-14b"
        await llm.aclose()

    async def test_json_mode_sets_response_format(self, httpserver: HTTPServer):
        captured: dict = {}

        def handler(request):
            captured["body"] = json.loads(request.get_data().decode())
            from werkzeug.wrappers import Response
            return Response(
                json.dumps(_completion_payload('{"k":1}')),
                content_type="application/json",
            )

        httpserver.expect_request(
            "/v1/chat/completions", method="POST",
        ).respond_with_handler(handler)

        llm = _make_llm(httpserver)
        await llm.complete("classifier", "give me json", json_mode=True)
        assert captured["body"]["response_format"] == {"type": "json_object"}
        await llm.aclose()

    async def test_images_sent_as_data_url(self, httpserver: HTTPServer):
        captured: dict = {}

        def handler(request):
            captured["body"] = json.loads(request.get_data().decode())
            from werkzeug.wrappers import Response
            return Response(
                json.dumps(_completion_payload("ok")),
                content_type="application/json",
            )

        httpserver.expect_request(
            "/v1/chat/completions", method="POST",
        ).respond_with_handler(handler)

        llm = _make_llm(httpserver)
        png = b"\x89PNG\r\n\x1a\nfake"
        await llm.complete("classifier", "look", images=[LLMImage(data=png, mime="image/png")])

        content = captured["body"]["messages"][0]["content"]
        assert content[0] == {"type": "text", "text": "look"}
        assert content[1]["type"] == "image_url"
        expected_url = f"data:image/png;base64,{base64.b64encode(png).decode()}"
        assert content[1]["image_url"]["url"] == expected_url
        await llm.aclose()

    async def test_model_override_bypasses_resolver(self, httpserver: HTTPServer):
        captured: dict = {}

        def handler(request):
            captured["body"] = json.loads(request.get_data().decode())
            from werkzeug.wrappers import Response
            return Response(
                json.dumps(_completion_payload("ok")),
                content_type="application/json",
            )

        httpserver.expect_request(
            "/v1/chat/completions", method="POST",
        ).respond_with_handler(handler)

        llm = _make_llm(httpserver)
        await llm.complete("classifier", "hi", model_override="forced-model")
        assert captured["body"]["model"] == "forced-model"
        await llm.aclose()


# ── complete(): error translation ────────────────────────────────────────

class TestErrorTranslation:
    """The SDK's error taxonomy is opaque to bots; LLM maps it to three
    user-actionable buckets: Unavailable, ModelNotFound, Timeout."""

    async def test_401_raises_unavailable(self, httpserver: HTTPServer):
        httpserver.expect_request(
            "/v1/chat/completions", method="POST",
        ).respond_with_data("unauthorized", status=401)

        llm = _make_llm(httpserver)
        with pytest.raises(LLMUnavailableError):
            await llm.complete("classifier", "hi")
        await llm.aclose()

    async def test_404_raises_model_not_found(self, httpserver: HTTPServer):
        httpserver.expect_request(
            "/v1/chat/completions", method="POST",
        ).respond_with_data("model not found", status=404)

        llm = _make_llm(httpserver)
        with pytest.raises(LLMModelNotFoundError):
            await llm.complete("classifier", "hi")
        await llm.aclose()

    async def test_500_raises_unavailable(self, httpserver: HTTPServer):
        httpserver.expect_request(
            "/v1/chat/completions", method="POST",
        ).respond_with_data("boom", status=500)

        llm = _make_llm(httpserver)
        with pytest.raises(LLMUnavailableError):
            await llm.complete("classifier", "hi")
        await llm.aclose()

    async def test_connection_refused_raises_unavailable(self):
        """No server listening — APIConnectionError -> Unavailable."""
        # Pick a port that's almost certainly closed.
        client = AsyncOpenAI(
            base_url="http://127.0.0.1:1/v1",
            api_key="not-needed",
            max_retries=0,
            timeout=2.0,
        )
        llm = LLM(client, namespace="test-bot")
        with pytest.raises(LLMUnavailableError):
            await llm.complete("classifier", "hi")
        await llm.aclose()

    async def test_timeout_raises_timeout(self, httpserver: HTTPServer):
        """Server stalls past the SDK's timeout -> LLMTimeoutError."""
        import time

        def slow_handler(request):
            time.sleep(0.5)
            from werkzeug.wrappers import Response
            return Response("{}", content_type="application/json")

        httpserver.expect_request(
            "/v1/chat/completions", method="POST",
        ).respond_with_handler(slow_handler)

        # Use a tight httpx timeout so the SDK raises APITimeoutError fast.
        client = AsyncOpenAI(
            base_url=httpserver.url_for("/v1"),
            api_key="not-needed",
            max_retries=0,
            timeout=httpx.Timeout(0.1),
        )
        llm = LLM(client, namespace="test-bot")
        with pytest.raises(LLMTimeoutError):
            await llm.complete("classifier", "hi")
        await llm.aclose()


# ── Namespace prefixing ──────────────────────────────────────────────────

class TestNamespace:
    """`namespace` is the role prefix used when callers pass a bare role.
    A role containing "/" is treated as a full path and used verbatim."""

    def test_bare_role_gets_namespace_prefix(self):
        llm = LLM(AsyncOpenAI(api_key="x"), namespace="archivist-bot")
        assert llm._full_role("classifier") == "archivist-bot/classifier"

    def test_qualified_role_passes_through(self):
        llm = LLM(AsyncOpenAI(api_key="x"), namespace="archivist-bot")
        assert llm._full_role("other-bot/synthesizer") == "other-bot/synthesizer"

    def test_no_namespace_passes_through(self):
        llm = LLM(AsyncOpenAI(api_key="x"))
        assert llm._full_role("classifier") == "classifier"


# ── from_env ─────────────────────────────────────────────────────────────

class TestFromEnv:
    def test_strips_trailing_chat_completions(self, monkeypatch):
        """oMLX docs sometimes show the full endpoint; we tolerate it."""
        monkeypatch.setenv("OPENAI_URL", "http://omlx.local/v1/chat/completions")
        monkeypatch.setenv("OPENAI_KEY", "secret")
        llm = LLM.from_env(namespace="x")
        assert str(llm._client.base_url).rstrip("/") == "http://omlx.local/v1"

    def test_empty_key_falls_back(self, monkeypatch):
        """Local AI is often unauthenticated; the SDK insists on *some* key
        so we substitute a placeholder."""
        monkeypatch.setenv("OPENAI_URL", "http://omlx.local/v1")
        monkeypatch.delenv("OPENAI_KEY", raising=False)
        llm = LLM.from_env()
        assert llm._client.api_key == "not-needed"

    def test_empty_url_raises_unavailable(self, monkeypatch):
        """Privacy guard: an unset OPENAI_URL must NOT silently fall through
        to api.openai.com (the SDK's default base_url). A family server
        with AI not configured should fail loudly with a setup hint, not
        ship OCR text to a hosted provider."""
        monkeypatch.delenv("OPENAI_URL", raising=False)
        monkeypatch.delenv("OPENAI_KEY", raising=False)
        with pytest.raises(LLMUnavailableError):
            LLM.from_env()

    def test_url_that_is_only_chat_completions_suffix_raises(self, monkeypatch):
        """Pathological config: the user pasted just '/chat/completions' as
        OPENAI_URL. After stripping the suffix nothing is left — treat it
        as unconfigured rather than firing off requests to a relative URL."""
        monkeypatch.setenv("OPENAI_URL", "/chat/completions")
        with pytest.raises(LLMUnavailableError):
            LLM.from_env()


# ── has_vision: caching ──────────────────────────────────────────────────

class TestHasVision:
    """The probe is the slowest call a bot makes on startup; the cache is
    what keeps it from re-paying that cost on every container restart."""

    async def test_probe_success_caches_true(self, httpserver: HTTPServer, tmp_path):
        cap = ModelCapabilities(path=tmp_path / "caps.json")
        httpserver.expect_request(
            "/v1/chat/completions", method="POST",
        ).respond_with_json(_completion_payload("ok"))

        llm = _make_llm(httpserver, capabilities=cap)
        assert await llm.has_vision() is True
        # And the answer survives a fresh ModelCapabilities pointed at
        # the same cache file — proves it hit disk.
        assert ModelCapabilities(path=tmp_path / "caps.json").supports_vision("test-model") is True
        await llm.aclose()

    async def test_vision_hint_error_caches_false(self, httpserver: HTTPServer, tmp_path):
        """A text-only model's rejection mentions 'image' / 'vision' /
        'multimodal' — we recognise that and cache the negative answer."""
        cap = ModelCapabilities(path=tmp_path / "caps.json")
        httpserver.expect_request(
            "/v1/chat/completions", method="POST",
        ).respond_with_data("This model does not support image inputs.", status=400)

        llm = _make_llm(httpserver, capabilities=cap)
        assert await llm.has_vision() is False
        assert ModelCapabilities(path=tmp_path / "caps.json").supports_vision("test-model") is False
        await llm.aclose()

    async def test_inconclusive_error_returns_false_uncached(self, httpserver: HTTPServer, tmp_path):
        """A transport-level 500 with no multimodal vocabulary in the body
        is ambiguous; report False but DON'T cache, so the next run retries."""
        cap = ModelCapabilities(path=tmp_path / "caps.json")
        httpserver.expect_request(
            "/v1/chat/completions", method="POST",
        ).respond_with_data("internal error", status=500)

        llm = _make_llm(httpserver, capabilities=cap)
        assert await llm.has_vision() is False
        assert ModelCapabilities(path=tmp_path / "caps.json").supports_vision("test-model") is None
        await llm.aclose()

    async def test_cached_answer_skips_probe(self, httpserver: HTTPServer, tmp_path):
        """A pre-recorded answer means zero HTTP traffic."""
        cap = ModelCapabilities(path=tmp_path / "caps.json")
        cap.record_vision("test-model", True)

        # No httpserver expectation registered — any call would explode.
        llm = _make_llm(httpserver, capabilities=cap)
        assert await llm.has_vision() is True
        await llm.aclose()


# ── Transcriber ──────────────────────────────────────────────────────────
#
# The Transcriber wraps the same SDK but points at whisper-server, which
# speaks /v1/audio/transcriptions. We pin transport-level behaviour the
# same way the LLM tests do: a pytest-httpserver mock for the endpoint,
# and assert that bytes go in as multipart and text comes back out.

def _make_transcriber(httpserver: HTTPServer, *, timeout: float = 5.0) -> Transcriber:
    """Build a Transcriber pointed at the local httpserver mock."""
    client = AsyncOpenAI(
        base_url=httpserver.url_for("/v1"),
        api_key="not-needed",
        max_retries=0,
        timeout=timeout,
    )
    return Transcriber(client, namespace="test-bot")


class TestTranscribe:
    """Happy path + error translation for the Transcriber."""

    async def test_returns_text_from_server(self, httpserver: HTTPServer):
        httpserver.expect_request(
            "/v1/audio/transcriptions", method="POST",
        ).respond_with_json({"text": "hello from whisper"})

        tr = _make_transcriber(httpserver)
        result = await tr.transcribe(b"fake-audio-bytes", filename="voice.ogg")
        assert result == "hello from whisper"
        await tr.aclose()

    async def test_strips_surrounding_whitespace(self, httpserver: HTTPServer):
        """whisper-server often returns text with a leading space; callers
        shouldn't have to remember to strip."""
        httpserver.expect_request(
            "/v1/audio/transcriptions", method="POST",
        ).respond_with_json({"text": "  hello  \n"})

        tr = _make_transcriber(httpserver)
        assert await tr.transcribe(b"fake-audio") == "hello"
        await tr.aclose()

    async def test_sends_multipart_with_filename(self, httpserver: HTTPServer):
        """The filename matters — whisper.cpp sniffs the container by
        extension, so 'voice.ogg' vs 'voice.wav' picks a different decoder.
        Pin that the SDK actually puts our filename on the wire."""
        captured: dict = {}

        def handler(request):
            captured["content_type"] = request.headers.get("Content-Type", "")
            captured["body"] = request.get_data()
            from werkzeug.wrappers import Response
            return Response(
                json.dumps({"text": "ok"}),
                content_type="application/json",
            )

        httpserver.expect_request(
            "/v1/audio/transcriptions", method="POST",
        ).respond_with_handler(handler)

        tr = _make_transcriber(httpserver)
        await tr.transcribe(b"PAYLOAD-BYTES", filename="memo.wav")

        assert captured["content_type"].startswith("multipart/form-data")
        assert b"memo.wav" in captured["body"]
        assert b"PAYLOAD-BYTES" in captured["body"]
        await tr.aclose()

    async def test_404_raises_unavailable(self, httpserver: HTTPServer):
        """Whisper has one model, so a 404 isn't 'model not found' — it's
        the endpoint being misconfigured. Surface as Unavailable, not the
        ModelNotFound the LLM uses."""
        httpserver.expect_request(
            "/v1/audio/transcriptions", method="POST",
        ).respond_with_data("not found", status=404)

        tr = _make_transcriber(httpserver)
        with pytest.raises(LLMUnavailableError):
            await tr.transcribe(b"audio")
        await tr.aclose()

    async def test_500_raises_unavailable(self, httpserver: HTTPServer):
        httpserver.expect_request(
            "/v1/audio/transcriptions", method="POST",
        ).respond_with_data("boom", status=500)

        tr = _make_transcriber(httpserver)
        with pytest.raises(LLMUnavailableError):
            await tr.transcribe(b"audio")
        await tr.aclose()

    async def test_connection_refused_raises_unavailable(self):
        """Whisper-server isn't running — APIConnectionError -> Unavailable."""
        client = AsyncOpenAI(
            base_url="http://127.0.0.1:1/v1",
            api_key="not-needed",
            max_retries=0,
            timeout=2.0,
        )
        tr = Transcriber(client, namespace="test-bot")
        with pytest.raises(LLMUnavailableError):
            await tr.transcribe(b"audio")
        await tr.aclose()

    async def test_timeout_raises_timeout(self, httpserver: HTTPServer):
        """A long voice memo can stall past the SDK's timeout; map to the
        same LLMTimeoutError so callers handle it uniformly with the LLM."""
        import time

        def slow_handler(request):
            time.sleep(0.5)
            from werkzeug.wrappers import Response
            return Response("{}", content_type="application/json")

        httpserver.expect_request(
            "/v1/audio/transcriptions", method="POST",
        ).respond_with_handler(slow_handler)

        client = AsyncOpenAI(
            base_url=httpserver.url_for("/v1"),
            api_key="not-needed",
            max_retries=0,
            timeout=httpx.Timeout(0.1),
        )
        tr = Transcriber(client, namespace="test-bot")
        with pytest.raises(LLMTimeoutError):
            await tr.transcribe(b"audio")
        await tr.aclose()


class TestTranscriberFromEnv:
    def test_strips_trailing_endpoint(self, monkeypatch):
        """The bot-runner env renders WHISPER_URL with the full endpoint;
        the SDK appends /audio/transcriptions itself, so we strip it."""
        monkeypatch.setenv(
            "WHISPER_URL", "http://host.docker.internal:42062/v1/audio/transcriptions"
        )
        tr = Transcriber.from_env(namespace="x")
        assert str(tr._client.base_url).rstrip("/") == "http://host.docker.internal:42062/v1"

    def test_accepts_v1_base(self, monkeypatch):
        """Setting just the /v1 root must also work — the SDK appends the
        endpoint either way."""
        monkeypatch.setenv("WHISPER_URL", "http://whisper.local/v1")
        tr = Transcriber.from_env()
        assert str(tr._client.base_url).rstrip("/") == "http://whisper.local/v1"

    def test_empty_url_raises_unavailable(self, monkeypatch):
        """Privacy guard mirrors LLM.from_env: an unset WHISPER_URL must
        not silently fall through to api.openai.com (the SDK's default)."""
        monkeypatch.delenv("WHISPER_URL", raising=False)
        monkeypatch.delenv("WHISPER_KEY", raising=False)
        with pytest.raises(LLMUnavailableError):
            Transcriber.from_env()

    def test_empty_key_falls_back(self, monkeypatch):
        """Native whisper-server is unauthenticated; the SDK insists on
        *some* key so the constructor substitutes a placeholder."""
        monkeypatch.setenv("WHISPER_URL", "http://whisper.local/v1")
        monkeypatch.delenv("WHISPER_KEY", raising=False)
        tr = Transcriber.from_env()
        assert tr._client.api_key == "not-needed"
