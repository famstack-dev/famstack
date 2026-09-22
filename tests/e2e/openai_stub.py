"""Content-routed OpenAI stub for the integration rig.

The archivist talks to any OpenAI-compatible endpoint (oMLX, Ollama, or
real OpenAI). In tests we point it at a pytest-httpserver and queue
responses per CALL KIND, not per arrival order. A dispatcher reads the
prompt of each incoming request and routes it:

    classify    "Classify this document..." (documents room)
                "Summarize and tag this content..." (captures)
    reformat    "Reformat this OCR-scanned document..."
    rewrite     "You extract search keywords from a question..."
    synthesize  "You are answering a family member's question..."

Why not ordered expectations: they match URL + method only, so ANY LLM
call takes the next queued response. When the pipeline gains a call
(e.g. the PDF reformat pass from 4513ff8), it silently steals the
classify stub, the bot fails open, and the test burns its 60s poll
before dying with an unrelated message. Content routing makes theft
impossible; a call with no queued response is recorded and surfaced
fast via `raise_if_unexpected()` (called from the polling helpers) and
again at teardown via the `openai` fixture.

The vision capability probe ("Reply with the single word 'ok'.") is
answered inline without consuming a stub — whether it fires depends on
the on-disk capability cache, so tests must not have to account for it.

The route prefixes are single-sourced from the prompt builders in
stacklets/docs/bot/pipeline.py. When a prompt's opening line changes,
update the matching prefix here — the stub reports the call as
unroutable (loud, not silent), so drift can't hide.
"""

from __future__ import annotations

import json
import threading
from collections import deque

from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Request, Response

_VISION_PROBE_MARKER = "Reply with the single word 'ok'."

# (kind, prompt prefix) — see module docstring for the source of truth.
_ROUTES: tuple[tuple[str, str], ...] = (
    ("classify", "Summarize and tag this content for a personal knowledge vault"),
    ("classify", "Classify this document."),
    ("reformat", "Reformat this OCR-scanned document"),
    ("rewrite", "You extract search keywords from a question"),
    ("synthesize", "You are answering a family member's question"),
)

_KINDS = ("classify", "reformat", "rewrite", "synthesize")


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


def _prompt_of(body: dict) -> str:
    """Text of the first user message — plain string or multimodal parts."""
    msgs = body.get("messages") or []
    content = (msgs[0] or {}).get("content", "") if msgs else ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                return part.get("text") or ""
    return ""


def _route(prompt: str) -> str | None:
    head = prompt.lstrip()
    for kind, prefix in _ROUTES:
        if head.startswith(prefix):
            return kind
    return None


class OpenAIStub:
    """Per-test stub state: one response queue per call kind.

    Queue responses with `classify()` / `reformat()` / `rewrite()` /
    `synthesize()` before the bot action that triggers them. Polling
    helpers call `raise_if_unexpected()` each tick so a wrong call
    count fails the test in seconds, not after a 60s wait. The
    `openai` fixture calls `assert_done()` at teardown — registered
    stubs that never got consumed fail the test too, keeping the rig
    honest in both directions.
    """

    def __init__(self, server: HTTPServer):
        self._server = server
        self._lock = threading.Lock()
        self._queues: dict[str, deque[str]] = {k: deque() for k in _KINDS}
        self._unavailable = False
        self.errors: list[str] = []
        server.expect_request(
            "/v1/chat/completions", method="POST",
        ).respond_with_handler(self._dispatch)

    def url_for(self, suffix: str) -> str:
        return self._server.url_for(suffix)

    # ── dispatcher (runs on the httpserver thread) ──────────────────────

    def _dispatch(self, request: Request) -> Response:
        if self._unavailable:
            return Response("service unavailable", status=503)
        try:
            body = json.loads(request.get_data(as_text=True) or "{}")
        except ValueError:
            body = {}
        prompt = _prompt_of(body)
        if prompt.lstrip().startswith(_VISION_PROBE_MARKER):
            return Response(
                json.dumps(_chat_completion("ok")),
                content_type="application/json",
            )
        head = " ".join(prompt.split())[:120]
        kind = _route(prompt)
        with self._lock:
            if kind is None:
                self.errors.append(f"unroutable LLM call: {head!r}")
                return Response("openai stub: no route for prompt", status=500)
            queue = self._queues[kind]
            if not queue:
                self.errors.append(
                    f"unexpected {kind} call (no stub queued): {head!r}"
                )
                return Response(f"openai stub: {kind} queue empty", status=500)
            content = queue.popleft()
        return Response(
            json.dumps(_chat_completion(content)),
            content_type="application/json",
        )

    # ── queueing ─────────────────────────────────────────────────────────

    def classify(self, payload: dict) -> None:
        """Queue one classification — `payload` is JSON-encoded into the
        message content; the bot parses keys like `title`, `tags`."""
        self._queues["classify"].append(json.dumps(payload))

    def reformat(self, markdown: str) -> None:
        """Queue one reformat pass — the bot uses `markdown` verbatim
        (subject to its own keep-most-of-the-input ratio guard)."""
        self._queues["reformat"].append(markdown)

    def rewrite(self, keywords: list[str]) -> None:
        """Queue one search-keyword rewrite: `{"keywords": [...]}`."""
        self._queues["rewrite"].append(json.dumps({"keywords": keywords}))

    def synthesize(self, text: str) -> None:
        """Queue one Q&A synthesis answer, returned verbatim."""
        self._queues["synthesize"].append(text)

    def set_unavailable(self) -> None:
        """Simulate an LLM outage — every call gets 503 from now on."""
        self._unavailable = True

    # ── fail fast / teardown ─────────────────────────────────────────────

    def raise_if_unexpected(self) -> None:
        """Abort immediately when the bot made a call we can't serve."""
        with self._lock:
            if self.errors:
                raise AssertionError(
                    "OpenAI stub got LLM calls it can't serve:\n  - "
                    + "\n  - ".join(self.errors)
                )

    def assert_done(self) -> None:
        """Teardown check: no unexpected calls, no unconsumed stubs."""
        self.raise_if_unexpected()
        leftovers = {k: len(q) for k, q in self._queues.items() if q}
        if leftovers:
            raise AssertionError(
                f"stubbed LLM responses never consumed: {leftovers} — "
                "the bot made fewer calls than the test queued"
            )


# ── Back-compat helpers (existing tests import these) ────────────────────

def stub_classify(stub: OpenAIStub, payload: dict) -> None:
    stub.classify(payload)


def stub_reformat(stub: OpenAIStub, markdown: str) -> None:
    stub.reformat(markdown)


def stub_unavailable(stub: OpenAIStub) -> None:
    stub.set_unavailable()
