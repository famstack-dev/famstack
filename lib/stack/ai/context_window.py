"""The context window of a model, as the AI server reports it now.

A job that builds a long prompt (a wiki page, a long document, the
agent's conversation) needs to know how many prompt tokens the model
accepts, so it can stay under that limit instead of failing. This module
asks the AI server for that number. Callers reach it through the chat
client, which resolves a role to its model first:

    llm = LLM.from_env(namespace="memory-curator")
    window = await llm.context_window("wiki")     # 262144, or None
    budget = 24_000 if window is None else min(24_000, window)

Worked example. The role `memory-curator/wiki` resolves to the model
`Qwen3.5-9B-MLX-4bit`. The client sends one request to the AI server:

    GET /v1/models/status            (with the API key)

and oMLX answers with one entry per model it knows:

    {"models": [
      {"id": "Qwen3.5-9B-MLX-4bit", "max_context_window": 262144, ...},
      {"id": "whisper-large-v3",    "max_context_window": 32768,  ...}
    ]}

The entry whose `id` (or `model_alias`) is the role's model gives
262144. A server without that endpoint, a server that does not answer,
or a model that is not in the list or has no window gives `None`:
unknown. It is never an error, because the window is extra knowledge
for a caller. A missing window must not stop a job that works without it.

Why it asks the server, every time it is asked:

- The limit is a setting of the AI server, not of the stack. The admin
  can change it in oMLX's admin UI at any time, without a `stack up`.
  oMLX resolves it per model: a per-model setting wins, else the model's
  own length (capped by a policy when one is set), else a global
  fallback. `/v1/models/status` reports the result of the same function
  that checks a prompt (`get_max_context_window` in oMLX 0.6.4 and 0.7.0).
  The per-model settings alone (`OMLXClient.get_model_settings` in the AI
  stacklet) miss the model's own length and the policy cap.
- Nothing here keeps the answer. A copy on disk or in `.env` would go
  stale without anyone noticing. The caller holds the number for one job
  (a wiki build, a filing) and asks again for the next one, so a change
  in the admin UI applies to the next job.

What it does not do:

- It does not count a prompt's tokens. Callers estimate.
- It asks only oMLX's endpoint. Other servers (Ollama, a cloud API) do
  not have it and give `None`.
- The window is a ceiling, not a budget. A 95,000-token prompt fits a
  262,144-token window and still holds the AI server for minutes, so a
  caller that cares about speed keeps its own smaller budget and uses
  the smaller of the two.

Container-only, like `stack.ai.client`: it uses the `openai` SDK's
client, so the request goes to the same address with the same API key
as the chat calls. Context: issue #342.
"""

from __future__ import annotations

import openai
from loguru import logger
from openai import AsyncOpenAI

__all__ = ["reported_window"]


# The status endpoint answers from oMLX's memory, so a few seconds is
# plenty. The chat client's own read timeout is 900 s (a long prefill);
# a job must not wait that long for a number it can do without.
STATUS_TIMEOUT_S = 10.0

# oMLX's extended model list. The path is relative to the client's base
# URL, which ends in /v1.
STATUS_PATH = "/models/status"


async def reported_window(client: AsyncOpenAI, model: str) -> int | None:
    """The context window the AI server reports for `model`, in tokens.

    Sends one `GET /v1/models/status` through `client` (its address and
    API key), with a short timeout and no retry. Keeps nothing.

    Returns the model's `max_context_window` as an int. Returns `None`
    (unknown) when the server has no such endpoint, does not answer,
    answers with something that is not a model list, or lists no
    positive window for `model`. Never raises for any of these.

    Example, against an oMLX that lists the model with 262144:

        await reported_window(client, "Qwen3.5-9B-MLX-4bit")   # 262144
        await reported_window(client, "not-downloaded")        # None
    """
    status = await _status(client)
    return None if status is None else _window_in(status, model)


# ── Asking the server ───────────────────────────────────────────────────


async def _status(client: AsyncOpenAI) -> object | None:
    """The parsed answer of `GET /v1/models/status`, or `None` when the
    server gives none (no endpoint, an error status, no connection, a
    timeout). The SDK raises `openai.APIError` for each of those."""
    asking = client.with_options(timeout=STATUS_TIMEOUT_S, max_retries=0)
    try:
        return await asking.get(STATUS_PATH, cast_to=object)
    except openai.APIError as error:
        logger.debug("[llm] no context window from the AI server: {}", error)
        return None


# ── Reading the status answer ───────────────────────────────────────────


def _window_in(status: object, model: str) -> int | None:
    """The `max_context_window` of `model` in a parsed status answer.

    Matches the entry's `id`, or its `model_alias` (oMLX accepts a
    request under either). Anything that is not the documented shape, or
    a window that is not a positive int, is `None`.

    >>> _window_in({"models": [{"id": "a", "max_context_window": 8192}]}, "a")
    8192
    >>> _window_in({"models": [{"id": "a", "max_context_window": None}]}, "a") is None
    True
    """
    models = status.get("models") if isinstance(status, dict) else None
    if not isinstance(models, list):
        return None
    for entry in models:
        if isinstance(entry, dict) and model in (entry.get("id"), entry.get("model_alias")):
            return _positive_int(entry.get("max_context_window"))
    return None


def _positive_int(value: object) -> int | None:
    """`value` when it is a positive int, else `None`. A bool is an int in
    Python, and `True` is no window."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None
