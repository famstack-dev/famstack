"""The AI's answer: whether the AI server `[ai]` names answers one tiny question.

    from stacklets.ai import answer

    answer.check(instance_dir, ai)    # asks and keeps: {"ok": True, "model": "...", "address": "..."}
    answer.check_when_ready(instance_dir, ai)   # the same, once the server lists its models
    answer.ask(instance_dir, ai)      # asks, keeps nothing

A server can list its models and still fail every completion: an oMLX
built for a newer macOS lists them and fails when it loads its Metal
kernels. The stack's health check asks only for the model list, so it
passes, and the archivist later files a letter without tags. So the AI
is asked one tiny question (`stack.ai.probe.answers`, with `[ai]
default`, the model every bot falls back to) in these places, which all
call `check`:

    stack up ai          the last step of the AI's run (`hooks/on_start_ready.py`)
    stack ai switch      once it wrote [ai] for a server elsewhere
    stack ai key new     once oMLX answers again after its restart
    stack ai check       the terminal's way to ask

The answer is kept in the instance by the framework's store
(`stack.ai.kept_answer`), which the setup page reads through `stack ai
check --last`, and `stack list` and `stack status` read to show the AI
degraded while the kept answer failed. Only an answer that failed keeps
the AI step from done; one that is missing is unknown, and the AI counts
as the stack's state says.

The question goes out with the key the bots use, so it goes out as
theirs do. A failure has a `reason`, by the HTTP status of the server's
error answer, because each has its own fix:

    server        HTTP 5xx: the server cannot compute (an oMLX build for
                  another macOS fails so)
    model         HTTP 404: the server does not have the model
    key           HTTP 401 or 403: the server refused the API key
    timeout       no answer within `stack.ai.probe.ANSWER_SECONDS`: a
                  first load of a large model can take that long
    unreachable   nothing answered, or the answer made no sense

What it does not check: each bot's own model (`[ai.models.<bot>]`), or
the address the bots use from their containers. The question goes to
`[ai] default` at the address the host uses.
"""

from __future__ import annotations

from pathlib import Path

from stack.ai.kept_answer import keep, setting

__all__ = ["REASONS", "READY_SECONDS", "check", "check_when_ready", "ask"]

# What kind of failure an answer is, by the HTTP status of the server's error.
REASONS = {401: "key", 403: "key", 404: "model"}
# How long a server that just restarted may take to list its models again
# before `check_when_ready` asks; brew restarts oMLX in about that time.
READY_SECONDS = 60


def check(instance_dir: str | Path, ai: dict) -> dict:
    """Ask the AI server `ai` (the `[ai]` section) one tiny question and
    keep the answer for this `[ai]`; returns it. An answer that is ok also
    says whether the server is an oMLX (`omlx.is_omlx`), asked without a key.

        check("/Users/homer/famstack", {"openai_url": "http://localhost:8000/v1",
                                        "openai_key": "omlx-key", "default": "qwen3:8b"})
        # {"ok": True, "model": "qwen3:8b", "address": "http://localhost:8000/v1",
        #  "omlx": True}
    """
    from .omlx import is_omlx

    found = ask(instance_dir, ai)
    if found["ok"]:
        # One request more, while the server is asked anyway: an oMLX gets
        # the settings recommended for this Mac as hints on the setup page.
        found = {**found, "omlx": is_omlx(str(ai.get("openai_url", "")))}
    keep(instance_dir, ai, found)
    return found


def check_when_ready(instance_dir: str | Path, ai: dict,
                     seconds: float = READY_SECONDS) -> dict | None:
    """`check`, once the server lists its models again, as after a
    restart; None, keeping nothing, when it does not within `seconds`:
    a server that is still starting has not failed.

        check_when_ready("/Users/homer/famstack", ai)   # {"ok": True, ...}
    """
    from stack.docker import wait_for_health

    address, _model, key = setting(instance_dir, ai)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    if wait_for_health(f"{address.rstrip('/')}/models", timeout=int(seconds), interval=1,
                       headers=headers) != "ready":
        return None
    return check(instance_dir, ai)


def ask(instance_dir: str | Path, ai: dict) -> dict:
    """The AI server's answer to one tiny question, kept nowhere: `ok`
    True with the `model` and the `address`, or `ok` False with the
    server's error in one line (`server_error`) and its `reason`.

        ask("/Users/homer/famstack", {"openai_url": "http://localhost:8000/v1", "default": "qwen3:8b"})
        # {"ok": False, "reason": "server", "model": "qwen3:8b", "status": 500,
        #  "address": "http://localhost:8000/v1", "server_error": "HTTP 500: ..."}

    `status` is the HTTP status of the server's error answer, 0 when none
    came (a timeout, nothing at the address).
    """
    from stack.ai.probe import answers

    address, model, key = setting(instance_dir, ai)
    reply = answers(address, key, model)
    if not reply.error:
        return {"ok": True, "model": model, "address": address}
    return {"ok": False, "model": model, "address": address, "server_error": reply.error,
            "reason": _reason(reply), "status": 0 if reply.timed_out else reply.status}


# ── Details ─────────────────────────────────────────────────────────────


def _reason(reply) -> str:
    """The kind of failure of `reply` (`stack.ai.probe.Reply`)."""
    if reply.timed_out:
        return "timeout"
    if reply.status >= 500:
        return "server"
    return REASONS.get(reply.status, "unreachable")
