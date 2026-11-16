"""The AI's kept answer: what the AI server `[ai]` names said to the stack's one tiny question.

    from stack.ai import kept_answer

    kept_answer.keep(instance_dir, ai, found)   # keep an answer for this [ai]
    kept_answer.stored(instance_dir, ai)        # what was kept for this [ai], or None
    kept_answer.refused(instance_dir, ai)       # the line to show when that answer failed
    kept_answer.said(found)                     # one line for a run step or the terminal

The ai stacklet asks the question (`stacklets/ai/answer.py`, `stack ai
check`, the last step of `stack up ai`) and keeps the answer here, in
`.stack/ai.answer.json`, under a hash of the address, the model and the
key it was asked with:

    {"setting": "5f1c…", "ok": false, "reason": "server", "model": "...",
     "address": "http://localhost:8000/v1", "status": 507,
     "server_error": "HTTP 507: Cannot load qwen3:8b"}

The store is part of the framework because two sides read it: the setup
page, through `stack ai check --last`, and `stack list` and `stack
status`, which show the AI degraded while the kept answer is a lasting refusal
(`refused`). A
server can list its models and still refuse every answer (a model that
does not fit its memory, an oMLX built for a newer macOS), so the health
check on the model list alone shows such a server healthy.

`stored` returns an answer only while `[ai]` still hashes the same. A
restart of anything keeps it; a switch, another model or a new key does
not match, so the AI counts as not asked until something asks again.
The key is never written down, only a hash of it together with the
address and the model. The file is written whole or not at all (a
temporary file, then `os.replace`), so a reader never sees half of it.

The key is the one the bots use (`stack.ai.server.openai_key`: the
stack's `AI_API_KEY` secret, else `[ai] openai_key`).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

__all__ = ["FILE", "LASTING_REFUSALS", "keep", "stored", "refused", "said", "setting"]

# Where the answer is kept, in the instance.
FILE = Path(".stack") / "ai.answer.json"


# ── Keeping and reading ─────────────────────────────────────────────────

def keep(instance_dir: str | Path, ai: dict, found: dict) -> None:
    """Keep `found`, the AI's answer to the ai stacklet's question, for this `[ai]`, in place of the
    one kept before. Writes `FILE` in the instance.

        keep("/Users/homer/famstack", ai, {"ok": True, "model": "qwen3:8b", "address": "..."})
    """
    path = Path(instance_dir) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"setting": _hash(instance_dir, ai), **found}, indent=2) + "\n"
    # Whole or not at all: a page that reads meanwhile sees the old answer.
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def stored(instance_dir: str | Path, ai: dict) -> dict | None:
    """The answer kept for this `[ai]` (its address, model and key), None
    when none was kept or `[ai]` changed since.

        stored("/Users/homer/famstack", ai)   # {"ok": True, "model": "qwen3:8b", ...}
    """
    try:
        kept = json.loads((Path(instance_dir) / FILE).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(kept, dict) or kept.pop("setting", None) != _hash(instance_dir, ai):
        return None
    return kept


# The error answers that last until someone changes something: a key the
# server refuses (401, 403), a model it does not have (404), a model it
# cannot load into its memory (507). Another 5xx (a busy or restarting
# server), a timeout (a first load of a large model) or no answer at all
# may pass by the next question, so they keep the AI's state as the
# stack's checks find it.
LASTING_REFUSALS = (401, 403, 404, 507)


def refused(instance_dir: str | Path, ai: dict) -> str | None:
    """The line a status shows while the answer kept for this `[ai]` is a
    lasting refusal of the server (`LASTING_REFUSALS`, by the `status` the
    ai stacklet keeps), naming the command that asks again. None when it
    answered, when nothing was kept for this `[ai]` (unknown is not a
    failure), or when the failure may pass. A later answer that is kept
    replaces it, so it clears with the next good answer.

        refused("/Users/homer/famstack", ai)
        # "The AI server cannot answer: HTTP 507: Cannot load qwen3:8b. Ask again with: ./stack ai check"
    """
    kept = stored(instance_dir, ai)
    if kept is None or kept.get("ok") or kept.get("status") not in LASTING_REFUSALS:
        return None
    return f"{said(kept)}. Ask again with: ./stack ai check"


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

def setting(instance_dir: str | Path, ai: dict) -> tuple[str, str, str]:
    """What the question depends on, and what the kept answer is kept
    under: the address, the model, and the key the bots send
    (`stack.ai.server.openai_key`).

        setting(instance_dir, {"openai_url": "http://localhost:8000/v1", "default": "qwen3:8b"})
        # ("http://localhost:8000/v1", "qwen3:8b", "local")
    """
    from stack.ai.server import openai_key
    from stack.secrets import TomlSecretStore

    secret = TomlSecretStore(Path(instance_dir) / ".stack" / "secrets.toml").get("", "AI_API_KEY")
    return (str(ai.get("openai_url", "")), str(ai.get("default", "")), openai_key(ai, secret))


def _hash(instance_dir: str | Path, ai: dict) -> str:
    """A hash of `setting`. The key itself is never kept."""
    return hashlib.sha256("\n".join(setting(instance_dir, ai)).encode()).hexdigest()
