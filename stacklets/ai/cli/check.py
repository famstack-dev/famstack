"""stack ai check: whether the AI server the stack uses answers a question.

    stack ai check            # ✓ The AI answers with mlx-community/Qwen3.5-9B-MLX-4bit
    stack ai check --json     # {"ok": true, "model": "...", "address": "http://localhost:8000/v1"}

A server that lists its models can still fail every completion: an oMLX
built for a newer macOS lists them and fails when it loads its Metal
kernels. The stack's health check asks only for the models, so it passes,
and the archivist later files a letter without tags. This command asks
the server `[ai] openai_url` one tiny question with `[ai] default`, the
model the bots fall back to, and says whether a reply came back
(`stack.ai.probe.answers`). It reads `stack.toml` and changes nothing.

When the server fails, the answer is an error, so the command exits 1:

    {"ok": false, "error": "The AI at http://localhost:8000/v1 did not answer: HTTP 500: ...",
     "server_error": "HTTP 500: Failed to load the default metallib. library not found",
     "reason": "server", "model": "...", "address": "http://localhost:8000/v1"}

`server_error` is the server's own error in one line, which the setup
page shows as it is. `reason` says what kind of failure it is, because
each has its own fix:

    server        HTTP 5xx: the server cannot compute (an oMLX build for
                  another macOS fails so)
    model         HTTP 404: the server does not have the model
    key           HTTP 401 or 403: the server refused the API key
    timeout       no answer in time: a large model may still be loading
    unreachable   nothing answered, or the answer made no sense

The setup's AI step runs this command through the host API (`ai_check`)
and says the AI is ready only after it passed.

What it does not check: the model of each bot. A bot may use its own
model (`[ai.models.<bot>]`), and the bots reach the server from their
containers at its Docker address. The check asks `[ai] default` at the
address the host uses, which is the model every bot falls back to.
"""

__all__ = ["HELP", "REASONS", "run"]

HELP = "Ask the AI server one tiny question: does it answer?"

# What kind of failure a check is, by the HTTP status of the error answer.
REASONS = {401: "key", 403: "key", 404: "model"}


def run(args, stacklet, config):
    """Ask the AI server `[ai]` names one question. Returns `ok` True with
    the model and the address, or an error with the server's own error
    (`server_error`) and its kind (`reason`). Prints one line for people; with `--json` that line
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
                "reason": _reason(reply),
                "error": f"The AI at {address} did not answer: {reply.error}"}
    print(f"  ✓ The AI answers with {model or 'its default model'} at {address}")
    return {"ok": True, "model": model, "address": address}


def _reason(reply) -> str:
    """The kind of failure of `reply` (`stack.ai.probe.Reply`), one of the
    module header's reasons."""
    if reply.timed_out:
        return "timeout"
    if reply.status >= 500:
        return "server"
    return REASONS.get(reply.status, "unreachable")
