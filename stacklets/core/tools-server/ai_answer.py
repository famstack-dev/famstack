"""The AI's answer: what `stack ai check` last said of the AI server the stack uses.

Step 4 of the setup page says "Your AI is ready" only after the AI
answered one short question, because a server can list its models and
still fail every completion (an oMLX built for a newer macOS does). The
check takes up to a minute while a model loads, longer than a page should
wait, so the setup page's watch address runs it (`setup_page.AI_ANSWERS`)
and this module keeps the result for the page read after it.

    state = state_of(listing)          # ("http://localhost:8000/v1", True): it lists its models
    last(state)                        # None: nobody asked yet
    record(state, {"ok": False, "server_error": "HTTP 500: ...", "model": "..."})
    last(state)                        # {"ok": False, "server_error": "HTTP 500: ...", ...}
    last(("http://192.168.1.30:11434/v1", True))   # None: another server, asked again

    /setup?step=ai ── no answer yet ──▶ "famstack checks your AI" (watches AI_ANSWERS)
    AI_ANSWERS ── stack ai check ──▶ record() ──▶ {"done": true} ──▶ the page reads again

Why it is kept this way:

- **Per AI server and its health.** The answer belongs to the state
  `stack list` reported when it came (`state_of`): the AI's address, and
  whether the stack's health check of it passes (it lists its models).
  After a switch to another server, or once a server that did not list
  its models does, the step asks again by itself. A check that failed
  while the server was down is thus not shown once it is up, and right
  after the AI's run, while the health check still fails, the step asks
  the AI itself instead of waiting for a reload.
- **In memory.** A restart of core forgets it, and the next visit to the
  AI step asks again: until it answered, the AI step is not done
  (`setup_page._ai_answers`), and the dashboard's "Continue setup" leads
  there. Nothing is written to disk.
- **"Check again" forgets it** (`forget`), so the step asks again after
  the admin changed the server.
- **Only an answer counts.** A reply of the AI (`model`) or its error
  (`server_error`) is kept; the host API's own failures are not, and
  neither is a check that ran out of time, because a large model may
  still be loading: the page goes on checking.
- **One check at a time** (`ask_once`). Two open pages, or one page read
  again while its watch still waits, would each start a completion; a
  caller that comes while a check runs gets that check's answer.

Pure but for the one answer it keeps and the one check it runs at a
time: no web framework; the check itself is the caller's.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

__all__ = ["state_of", "record", "last", "forget", "ask_once"]

# The one answer kept: the state it came in, and what the check said.
_last: tuple[tuple[str, bool], dict] | None = None
# One check at a time, and when the last one ended with what it got. A lock,
# not an asyncio task, because the check runs in a worker thread.
_asking = threading.Lock()
_asked: tuple[float, dict] = (float("-inf"), {})


def state_of(listing: dict | None) -> tuple[str, bool]:
    """What the listing says of the AI: the address of a server the stack
    does not manage (the ai row's `remote`; "" for the engine the stack
    runs itself), and whether the stack's health check of it passes (the
    row is not `degraded`).

        state_of({"stacklets": [{"id": "ai", "remote": "http://localhost:8000/v1"}]})
        # ("http://localhost:8000/v1", True)
        state_of({"stacklets": [{"id": "ai", "remote": "http://localhost:8000/v1",
                                 "degraded": True}]})
        # ("http://localhost:8000/v1", False)
    """
    ai = next((s for s in (listing or {}).get("stacklets", []) if s.get("id") == "ai"), {})
    return ai.get("remote", ""), not ai.get("degraded")


def record(state: tuple[str, bool], answer: dict) -> bool:
    """Keep `answer`, what `stack ai check` said, for `state`; returns
    whether it is an answer: `ok` True with the `model`, or `ok` False
    with the `server_error`, for any reason but "timeout". The host API's
    own errors (it did not answer, a crash, output that is not JSON) and
    a check that ran out of time keep nothing, so the page goes on
    checking.

        record(("", True), {"ok": True, "model": "Qwen3.5-9B-MLX-4bit"})   # True
        record(("", True), {"error": "Command timed out: stack ai_check"}) # False
        record(("", True), {"ok": False, "server_error": "No answer ... within 90 seconds",
                            "reason": "timeout"})                          # False
    """
    global _last
    if not _is_answer(answer):
        return False
    _last = (state, answer)
    return True


def last(state: tuple[str, bool]) -> dict | None:
    """What the check last said in `state`, None while it was not asked
    since core started, or asked in another state.

        last(("", True))    # {"ok": True, "model": "Qwen3.5-9B-MLX-4bit"}
    """
    if _last is None or _last[0] != state:
        return None
    return _last[1]


def ask_once(check: Callable[[], dict]) -> dict:
    """Run `check` (the host API's `ai_check`), one at a time: a caller
    that comes while a check runs waits for it and gets its answer,
    instead of asking the AI a second time. A caller that comes after it
    ended runs a new one. Blocks, so call it from a worker thread.

        ask_once(lambda: client.call({"cmd": "ai_check"}, timeout=120))
        # {"ok": True, "model": "Qwen3.5-9B-MLX-4bit", ...}
    """
    global _asked
    came = time.monotonic()
    with _asking:
        ended, answer = _asked
        if ended >= came:
            return answer
        answer = check()
        _asked = (time.monotonic(), answer)
        return answer


def forget() -> None:
    """Forget the answer: "Check again", a fresh start of the server, a test.

        forget()
        last(("", True))    # None
    """
    global _last
    _last = None


def _is_answer(answer: dict) -> bool:
    """Whether `answer` is what the AI said: a reply with its model, or
    an error of the server's that is not a timeout."""
    if answer.get("ok") is True:
        return "model" in answer
    return (answer.get("ok") is False and bool(answer.get("server_error"))
            and answer.get("reason") != "timeout")
