"""The AI client gives a role's context window as the AI server reports it.

A stacklet author asks `LLM.context_window(role)` before building a long
prompt. These tests drive that entry point the way a job does, against a
stand-in oMLX on localhost (`pytest-httpserver`) that answers
`GET /v1/models/status` in the shape oMLX 0.6.4 sends: one entry per
model with its `max_context_window`. The stand-in sits at the process
boundary, where the real oMLX would be; nothing in the client is patched.

The role's model comes from the stack's `[ai.models]` table, set here as
the bot runner's environment would set it (`stack.ai.models`).
"""

from __future__ import annotations

import socket

import pytest
from openai import AsyncOpenAI
from pytest_httpserver import HTTPServer

import stack.ai.models
from stack.ai.client import LLM

WIKI_MODEL = "Qwen3.5-9B-MLX-4bit"
DEFAULT_MODEL = "Qwen3.5-4B-MLX-4bit"
API_KEY = "omlx-test-key"


@pytest.fixture(autouse=True)
def configured_models(monkeypatch):
    """The curator's wiki role has its own model; every other role falls
    back to the default. Two models on the server, so a test sees that the
    window comes from the role's model and not from the first entry."""
    monkeypatch.setattr(stack.ai.models, "_DEFAULT_MODEL", DEFAULT_MODEL)
    monkeypatch.setattr(stack.ai.models, "_MODELS", {"memory-curator.wiki": WIKI_MODEL})


def _client_for(base_url: str) -> LLM:
    """The chat client of the memory curator, pointed at `base_url`."""
    client = AsyncOpenAI(base_url=base_url, api_key=API_KEY, max_retries=0, timeout=5.0)
    return LLM(client, namespace="memory-curator")


def _llm(httpserver: HTTPServer) -> LLM:
    return _client_for(httpserver.url_for("/v1"))


def _status(*models: dict) -> dict:
    """An oMLX status answer listing `models`, trimmed to the fields that
    matter here plus one oMLX always sends."""
    return {"models": [{"loaded": False, **m} for m in models]}


def _serve_status(httpserver: HTTPServer, answer: dict) -> None:
    httpserver.expect_request(
        "/v1/models/status", method="GET",
        headers={"Authorization": f"Bearer {API_KEY}"},
    ).respond_with_json(answer)


# ── The server reports a window ─────────────────────────────────────────


async def test_a_role_gets_the_window_the_server_reports_for_its_model(httpserver: HTTPServer):
    """Given an oMLX that lists the wiki's model with 262144 and the default
    model with 32768, when the curator asks for the wiki role's window, then
    it gets 262144: the window of the model the role resolves to, which is
    the model its calls go to."""
    _serve_status(httpserver, _status(
        {"id": DEFAULT_MODEL, "max_context_window": 32768},
        {"id": WIKI_MODEL, "max_context_window": 262144},
    ))

    assert await _llm(httpserver).context_window("wiki") == 262144


async def test_a_role_without_its_own_model_gets_the_default_models_window(httpserver: HTTPServer):
    """Given no model of its own for the role, when asked, then the window is
    the default model's, because that is where its calls go."""
    _serve_status(httpserver, _status(
        {"id": WIKI_MODEL, "max_context_window": 262144},
        {"id": DEFAULT_MODEL, "max_context_window": 32768},
    ))

    assert await _llm(httpserver).context_window("summary") == 32768


async def test_a_model_requested_under_its_alias_gets_its_window(httpserver: HTTPServer):
    """Given oMLX lists the model under its folder name with the configured
    name as `model_alias`, when asked, then the window is found: oMLX
    accepts a request under either name."""
    _serve_status(httpserver, _status(
        {"id": "Qwen3.5-9B-MLX-4bit-dwq", "model_alias": WIKI_MODEL, "max_context_window": 131072},
    ))

    assert await _llm(httpserver).context_window("wiki") == 131072


async def test_a_window_changed_on_the_server_applies_to_the_next_job(httpserver: HTTPServer):
    """Given the admin lowers the wiki model's window in oMLX between two
    jobs, when the next job asks the same client, then it gets the new
    number, without a restart. The client keeps no copy that could go
    stale."""
    llm = _llm(httpserver)
    httpserver.expect_ordered_request("/v1/models/status").respond_with_json(
        _status({"id": WIKI_MODEL, "max_context_window": 262144}))
    httpserver.expect_ordered_request("/v1/models/status").respond_with_json(
        _status({"id": WIKI_MODEL, "max_context_window": 65536}))

    first_job = await llm.context_window("wiki")
    next_job = await llm.context_window("wiki")

    assert (first_job, next_job) == (262144, 65536)


# ── The window is unknown ───────────────────────────────────────────────


async def test_a_server_without_the_status_endpoint_gives_unknown(httpserver: HTTPServer):
    """Given a server that is not oMLX (no `/v1/models/status`, so 404),
    when asked, then the window is unknown (None), not an error."""
    httpserver.expect_request("/v1/models/status").respond_with_data("Not Found", status=404)

    assert await _llm(httpserver).context_window("wiki") is None


async def test_a_server_that_does_not_answer_gives_unknown():
    """Given nothing listens at the AI server's address, when asked, then the
    window is unknown, not an error: a job that can work without it goes on."""
    llm = _client_for(f"http://127.0.0.1:{_closed_port()}/v1")

    assert await llm.context_window("wiki") is None


async def test_a_server_error_gives_unknown(httpserver: HTTPServer):
    """Given an oMLX that is still starting (503 "Server not initialized"),
    when asked, then the window is unknown."""
    httpserver.expect_request("/v1/models/status").respond_with_json(
        {"detail": "Server not initialized"}, status=503)

    assert await _llm(httpserver).context_window("wiki") is None


@pytest.mark.parametrize("answer", [
    pytest.param(_status({"id": WIKI_MODEL, "max_context_window": None}), id="no-window"),
    pytest.param(_status({"id": DEFAULT_MODEL, "max_context_window": 32768}), id="model-not-listed"),
    pytest.param({"object": "list", "data": []}, id="not-a-status-answer"),
])
async def test_a_server_that_reports_no_window_for_the_model_gives_unknown(httpserver: HTTPServer, answer):
    """Given a status answer with no window for the role's model (oMLX sends
    None for a document converter; a model not downloaded is not listed; a
    server may answer in another shape), when asked, then the window is
    unknown. It is never another model's window."""
    httpserver.expect_request("/v1/models/status").respond_with_json(answer)

    assert await _llm(httpserver).context_window("wiki") is None


# ── Resting state ───────────────────────────────────────────────────────


async def test_nothing_asks_the_server_until_a_caller_asks(httpserver: HTTPServer):
    """Given a client that sends a chat call, when no
    caller asks for a window, then the server sees only the chat call: no
    existing call asks for the window on its own."""
    httpserver.expect_request("/v1/chat/completions", method="POST").respond_with_json({
        "id": "c", "object": "chat.completion", "model": WIKI_MODEL,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "ok"}}],
    })

    await _llm(httpserver).complete("wiki", "Say ok.")

    assert [request.path for request, _ in httpserver.log] == ["/v1/chat/completions"]


def _closed_port() -> int:
    """A localhost port nothing listens on: bound once, then released."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
