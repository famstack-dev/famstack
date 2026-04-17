"""Helpers that attach OpenAI-shape responses to a pytest-httpserver.

The archivist talks to any OpenAI-compatible endpoint (oMLX, Ollama, or
real OpenAI). In tests we point it at a pytest-httpserver and register
one ordered response per call. The archivist hits classify first, then
reformat — so tests register in that order.
"""

from __future__ import annotations

import json

from pytest_httpserver import HTTPServer


def _chat_completion(content: str, model: str = "test-model") -> dict:
    """OpenAI chat.completion response envelope."""
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


def stub_classify(server: HTTPServer, payload: dict) -> None:
    """Register the next `/v1/chat/completions` call to return `payload`
    encoded as JSON inside the message content.

    Use for classification responses — the archivist parses the returned
    content as JSON and reads keys like `title`, `topics`, `persons`.
    """
    server.expect_ordered_request(
        "/v1/chat/completions", method="POST",
    ).respond_with_json(_chat_completion(json.dumps(payload)))


def stub_reformat(server: HTTPServer, markdown: str) -> None:
    """Register the next `/v1/chat/completions` call to return `markdown`
    as the message content — the archivist uses it verbatim."""
    server.expect_ordered_request(
        "/v1/chat/completions", method="POST",
    ).respond_with_json(_chat_completion(markdown))


def stub_unavailable(server: HTTPServer) -> None:
    """Simulate an LLM outage — every call gets 503."""
    server.expect_request(
        "/v1/chat/completions", method="POST",
    ).respond_with_data("service unavailable", status=503)
