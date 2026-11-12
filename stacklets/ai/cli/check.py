"""stack ai check: whether the AI server the stack uses answers a question.

    stack ai check            # ✓ The AI answers with mlx-community/Qwen3.5-9B-MLX-4bit
    stack ai check --json     # {"ok": true, "model": "...", "address": "http://localhost:8000/v1"}

A server that lists its models can still fail every completion: an oMLX
built for a newer macOS lists them and fails when it loads its Metal
kernels. The stack's health check asks only for the models, so it passes,
and the archivist later files a letter without tags. This command asks
the server `[ai] openai_url` one tiny question with `[ai] default`, the
model the bots fall back to, and says whether a reply came back
(`stack.ai.probe.answers`). It changes nothing in `stack.toml`.

When the server fails, the answer is an error, so the command exits 1:

    {"ok": false, "error": "The AI server cannot answer: HTTP 500: ...",
     "server_error": "HTTP 500: Failed to load the default metallib. library not found",
     "reason": "server", "model": "...", "address": "http://localhost:8000/v1"}

The answer is kept in the instance, as the AI's run keeps it, and the
setup page reads it (`answer.py`, which also lists the reasons and what
the question does not check). `server_error` is the server's own error
in one line, which the page shows as it is.
"""

__all__ = ["HELP", "run"]

HELP = "Ask the AI server one tiny question: does it answer?"


def run(args, stacklet, config):
    """Ask the AI server `[ai]` names one question. Returns `ok` True with
    the model and the address, or an error with the server's own error
    (`server_error`) and its kind (`reason`). Keeps the answer either
    way. Prints one line for people; with `--json` that line goes to
    stderr. An error without asking when `[ai]` names no server.

        run([], stacklet, config)   # {"ok": True, "model": "qwen3:8b",
                                    #  "address": "http://192.168.1.30:11434/v1"}
    """
    from ..answer import check, said

    ai = config.get("stack", {}).get("ai", {})
    if not ai.get("openai_url"):
        return {"error": "No AI server is set in stack.toml [ai]. Run './stack up ai', or "
                         "'./stack ai switch <url>' for a server elsewhere."}
    found = check(config["instance_dir"], ai)
    if not found["ok"]:
        return {**found, "error": said(found)}
    print(f"  ✓ {said(found)} at {found['address']}")
    return found
