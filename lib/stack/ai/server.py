"""The AI server's key: the one rule for which key the stack sends to `[ai] openai_url`.

    from stack.ai.server import openai_key

    openai_key({"openai_key": "7fk2-pq9w-m3xd-h8ra"}, secret=None)    # "7fk2-pq9w-m3xd-h8ra"
    openai_key({"openai_key": "7fk2-pq9w-m3xd-h8ra"}, secret="sk-1")  # "sk-1"

The stack's `AI_API_KEY` secret, when one is set, wins over `[ai]
openai_key`. The containers get the key this rule gives (the template
variable `{ai_openai_key}`, `Stack._build_template_vars`), and the AI
stacklet asks the AI with the same key (`stacklets/ai/answer.py`), so a
question the stack asks goes out as the bots' questions go out.

Stdlib only, so the host's command line can import it.
"""

from __future__ import annotations

__all__ = ["openai_key"]


def openai_key(ai: dict, secret: str | None) -> str:
    """The key the stack sends to the AI server: `secret` (the stack's
    `AI_API_KEY`) when it is set, else `[ai] openai_key`, else "".

        openai_key({"openai_key": "local"}, secret="")    # "local"
    """
    return secret or str(ai.get("openai_key", "") or "")
