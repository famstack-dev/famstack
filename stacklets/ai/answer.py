"""The AI's answer: whether the AI server `[ai]` names answers one tiny question, kept in the instance.

    from stacklets.ai import answer

    answer.check(instance_dir, ai)    # asks and keeps: {"ok": True, "model": "...", "address": "..."}
    answer.stored(instance_dir, ai)   # what was kept for this [ai], or None
    answer.said(kept)                 # one line for a run step or the terminal

A server can list its models and still fail every completion: an oMLX
built for a newer macOS lists them and fails when it loads its Metal
kernels. The stack's health check asks only for the model list, so it
passes, and the archivist later files a letter without tags. So the AI
is asked one tiny question (`stack.ai.probe.answers`, with `[ai]
default`, the model every bot falls back to) in three places, which all
call `check`:

    stack up ai          the last step of the AI's run (`hooks/on_start_ready.py`)
    stack ai switch      once it wrote [ai] for a server elsewhere
    stack ai check       the terminal's way to ask

The answer is kept in `.stack/ai.answer.json`, next to the AI's other
state, under a hash of the address, the model and the key it was asked
with:

    {"setting": "5f1c…", "ok": false, "reason": "server", "model": "...",
     "address": "http://localhost:8000/v1",
     "server_error": "HTTP 500: Failed to load the default metallib. library not found"}

`stored` returns it only while `[ai]` still hashes the same. A restart
of anything keeps it; a switch, another model or a new key does not
match, so the AI counts as not asked until something asks again. The
setup page reads it through `stack ai recommended app` and says the AI
is ready only after an answer that is ok.

A failure has a `reason`, by the HTTP status of the server's error
answer, because each has its own fix:

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

The key is never written down, only a hash of it together with the
address and the model.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

__all__ = ["FILE", "REASONS", "check", "ask", "keep", "stored", "said"]

# Where the answer is kept, in the instance.
FILE = Path(".stack") / "ai.answer.json"
# What kind of failure an answer is, by the HTTP status of the server's error.
REASONS = {401: "key", 403: "key", 404: "model"}


def check(instance_dir: str | Path, ai: dict) -> dict:
    """Ask the AI server `ai` (the `[ai]` section) one tiny question and
    keep the answer for this `[ai]`; returns it.

        check("/Users/homer/famstack", {"openai_url": "http://localhost:8000/v1",
                                        "openai_key": "omlx-key", "default": "qwen3:8b"})
        # {"ok": True, "model": "qwen3:8b", "address": "http://localhost:8000/v1"}
    """
    found = ask(ai)
    keep(instance_dir, ai, found)
    return found


def ask(ai: dict) -> dict:
    """The AI server's answer to one tiny question, kept nowhere: `ok`
    True with the `model` and the `address`, or `ok` False with the
    server's error in one line (`server_error`) and its `reason`.

        ask({"openai_url": "http://localhost:8000/v1", "default": "qwen3:8b"})
        # {"ok": False, "reason": "server", "model": "qwen3:8b",
        #  "address": "http://localhost:8000/v1", "server_error": "HTTP 500: ..."}
    """
    from stack.ai.probe import answers

    address, model = ai.get("openai_url", ""), ai.get("default", "")
    reply = answers(address, ai.get("openai_key", ""), model)
    if not reply.error:
        return {"ok": True, "model": model, "address": address}
    return {"ok": False, "model": model, "address": address, "server_error": reply.error,
            "reason": _reason(reply)}


def keep(instance_dir: str | Path, ai: dict, found: dict) -> None:
    """Keep `found`, an answer of `ask`, for this `[ai]`, in place of the
    one kept before. Writes `FILE` in the instance.

        keep("/Users/homer/famstack", ai, {"ok": True, "model": "qwen3:8b", "address": "..."})
    """
    path = Path(instance_dir) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"setting": _setting(ai), **found}, indent=2) + "\n")


def stored(instance_dir: str | Path, ai: dict) -> dict | None:
    """The answer kept for this `[ai]` (its address, model and key), None
    when none was kept or `[ai]` changed since.

        stored("/Users/homer/famstack", ai)   # {"ok": True, "model": "qwen3:8b", ...}
    """
    try:
        kept = json.loads((Path(instance_dir) / FILE).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(kept, dict) or kept.pop("setting", None) != _setting(ai):
        return None
    return kept


def said(found: dict) -> str:
    """One line for a run step or the terminal, in English.

        said({"ok": True, "model": "qwen3:8b", "address": "..."})
        # "The AI answers with qwen3:8b"
        said({"ok": False, "reason": "model", "model": "qwen3:8b", "server_error": "HTTP 404: ..."})
        # "The AI server does not have the model qwen3:8b: HTTP 404: ..."
    """
    model = found.get("model") or "its default model"
    if found.get("ok"):
        return f"The AI answers with {model}"
    why = {"server": "The AI server cannot answer",
           "model": f"The AI server does not have the model {model}",
           "key": "The AI server refused the API key",
           "timeout": "The AI did not answer in time; a model may still be loading"}
    return f"{why.get(found.get('reason', ''), 'The AI did not answer')}: {found.get('server_error', '')}"


# ── Details ─────────────────────────────────────────────────────────────

def _setting(ai: dict) -> str:
    """A hash of what the answer depends on: the address, the model and
    the key. The key itself is never kept."""
    line = "\n".join(str(ai.get(k, "")) for k in ("openai_url", "default", "openai_key"))
    return hashlib.sha256(line.encode()).hexdigest()


def _reason(reply) -> str:
    """The kind of failure of `reply` (`stack.ai.probe.Reply`)."""
    if reply.timed_out:
        return "timeout"
    if reply.status >= 500:
        return "server"
    return REASONS.get(reply.status, "unreachable")
