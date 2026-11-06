"""A prompt over the model's context window fails with an error of its own.

oMLX refuses such a prompt with HTTP 400 before it starts the model. A
caller has to tell this from a server that is down: waiting does not help,
and the fix is a shorter prompt. These tests drive `LLM.complete` the way a
job does, against a stand-in oMLX on localhost (`pytest-httpserver`) that
answers with the error bodies of the real servers:

    oMLX 0.6.4  Prompt too long: N tokens exceeds max context window of M tokens
    oMLX 0.7.3  Prompt is too long: N tokens > M maximum context length

The stand-in sits at the process boundary, where the real oMLX would be.
Nothing in the client is patched.
"""

from __future__ import annotations

import pytest
from openai import AsyncOpenAI
from pytest_httpserver import HTTPServer

import stack.ai.models
from stack.ai.client import LLM, LLMError, LLMModelNotFoundError, LLMUnavailableError
from stacklets.docs.bot.pipeline import Classifier
from stacklets.memory.lib import rewrite_query

CHAT = "/v1/chat/completions"
OMLX_0_6 = "Prompt too long: 130010 tokens exceeds max context window of 90000 tokens"
OMLX_0_7 = "Prompt is too long: 130010 tokens > 90000 maximum context length"


@pytest.fixture(autouse=True)
def configured_model(monkeypatch):
    """One model for every role, as the bot runner's environment would set it."""
    monkeypatch.setattr(stack.ai.models, "_DEFAULT_MODEL", "Qwen3.5-9B-MLX-4bit")


def _llm(httpserver: HTTPServer) -> LLM:
    """A chat client pointed at the stand-in, as `LLM.from_env` would build it."""
    client = AsyncOpenAI(base_url=httpserver.url_for("/v1"), api_key="omlx-test-key",
                         max_retries=0, timeout=5.0)
    return LLM(client, namespace="memory-wiki")


def _refuse(httpserver: HTTPServer, message: str, status: int = 400) -> None:
    """Every chat call gets `message` in the error body oMLX sends."""
    httpserver.expect_request(CHAT, method="POST").respond_with_json(
        {"error": {"message": message, "type": "invalid_request_error",
                   "param": None, "code": None}},
        status=status,
    )


# ── A prompt over the window ────────────────────────────────────────────


@pytest.mark.parametrize("message", [OMLX_0_6, OMLX_0_7], ids=["omlx-0.6", "omlx-0.7"])
@pytest.mark.parametrize("stop_on_repeat", [False, True], ids=["whole", "streamed"])
async def test_the_caller_gets_both_sizes(httpserver: HTTPServer, message: str, stop_on_repeat: bool):
    """Given a server that refuses the prompt as too long, in the wording of
    either oMLX, when a caller asks for an answer, whole or streamed, then the
    error carries both sizes, as numbers and in its text, because a log line
    is where a person reads them."""
    _refuse(httpserver, message)

    with pytest.raises(LLMError) as caught:
        await _llm(httpserver).complete("wiki", "a long prompt", stop_on_repeat=stop_on_repeat)

    assert caught.value.prompt_tokens == 130010  # pyright: ignore[reportAttributeAccessIssue]
    assert caught.value.context_window == 90000  # pyright: ignore[reportAttributeAccessIssue]
    assert "130010" in str(caught.value) and "90000" in str(caught.value)


# ── Callers that survive a server error ─────────────────────────────────


async def test_a_search_goes_on_without_its_keywords(httpserver: HTTPServer):
    """Given a too-long refusal, when the memory rewrites a search question,
    then there are no keywords and no error: the question is searched as it
    was asked, as with any server failure."""
    _refuse(httpserver, OMLX_0_7)

    assert await rewrite_query("Where is the tent?", llm=_llm(httpserver)) == []


async def test_a_document_keeps_its_raw_text(httpserver: HTTPServer):
    """Given a too-long refusal, when a document's text is reformatted, then
    there is no result and no error: the pipeline keeps the raw text, as with
    any server failure."""
    _refuse(httpserver, OMLX_0_7)

    assert await Classifier(_llm(httpserver)).reformat("Invoice from Springfield Power") is None


# ── Every other failure stays what it was ───────────────────────────────


@pytest.mark.parametrize("message", [
    "Invalid temperature: 7",
    "Prompt too long",
    "Prompt too long: the tokenizer failed",
], ids=["other", "no-sizes", "no-numbers"])
async def test_another_bad_request_stays_unavailable(httpserver: HTTPServer, message: str):
    """Given an HTTP 400 that names no two sizes, when a caller asks, then the
    error is the plain `LLMUnavailableError`, because no sizes can be named."""
    _refuse(httpserver, message)

    with pytest.raises(LLMUnavailableError) as caught:
        await _llm(httpserver).complete("wiki", "a prompt")

    assert type(caught.value) is LLMUnavailableError
    assert "HTTP 400" in str(caught.value)


async def test_the_same_words_with_another_status_stay_unavailable(httpserver: HTTPServer):
    """Given a server error whose text happens to read like the refusal, when
    a caller asks, then it stays a server failure: only a 400 is the server
    refusing this prompt."""
    _refuse(httpserver, OMLX_0_7, status=422)

    with pytest.raises(LLMUnavailableError) as caught:
        await _llm(httpserver).complete("wiki", "a prompt")

    assert type(caught.value) is LLMUnavailableError


async def test_a_missing_model_stays_a_missing_model(httpserver: HTTPServer):
    """Given a 404, when a caller asks, then the model-not-found error stands."""
    _refuse(httpserver, "no such model", status=404)

    with pytest.raises(LLMModelNotFoundError):
        await _llm(httpserver).complete("wiki", "a prompt")
