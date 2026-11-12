"""stack ai check: whether the AI server the stack uses answers a question.

    stack ai check            # ✓ The AI answers with mlx-community/Qwen3.5-9B-MLX-4bit
    stack ai check --json     # {"ok": true, "model": "...", "address": "http://localhost:8000/v1"}

A server that lists its models can still fail every completion: an oMLX
built for a newer macOS lists them and fails when it loads its Metal
kernels. The stack's health check asks only for the models, so it passes,
and the archivist later files a letter without tags. This command asks
the server `[ai] openai_url` one tiny question with `[ai] default`, as the
stack's bots would ask it, and says whether a reply came back
(`stack.ai.probe.answers`). It reads `stack.toml` and changes nothing.

When the server fails, the answer is an error, so the command exits 1:

    {"ok": false, "error": "The AI at http://localhost:8000/v1 did not answer: HTTP 500: ...",
     "server_error": "HTTP 500: Failed to load the default metallib. library not found",
     "model": "...", "address": "http://localhost:8000/v1"}

`server_error` is the server's own error in one line, which the setup
page shows as it is. The setup's AI step runs this command through the
host API (`ai_check`) and says the AI is ready only after it passed.
"""

__all__ = ["HELP", "run"]

HELP = "Ask the AI server one tiny question: does it answer?"


def run(args, stacklet, config):
    """Ask the AI server `[ai]` names one question. Returns `ok` True with
    the model and the address, or an error with the server's own error
    (`server_error`). Prints one line for people; with `--json` that line
    goes to stderr. An error without asking when `[ai]` names no server.

        run([], stacklet, config)   # {"ok": True, "model": "qwen3:8b",
                                    #  "address": "http://192.168.1.30:11434/v1"}
    """
    from stack.ai.probe import answers

    ai = config.get("stack", {}).get("ai", {})
    address, model = ai.get("openai_url", ""), ai.get("default", "")
    if not address:
        return {"error": "No AI server is set in stack.toml [ai]. Run './stack up ai', or "
                         "'./stack ai switch <url>' for a server elsewhere."}
    reply = answers(address, ai.get("openai_key", ""), model)
    if reply.error:
        return {"ok": False, "model": model, "address": address, "server_error": reply.error,
                "error": f"The AI at {address} did not answer: {reply.error}"}
    print(f"  ✓ The AI answers with {model or 'its default model'} at {address}")
    return {"ok": True, "model": model, "address": address}
