"""stack ai check: whether the AI server the stack uses answers a question.

    stack ai check                # ✓ The AI answers with mlx-community/Qwen3.5-9B-MLX-4bit
    stack ai check --json         # {"ok": true, "model": "...", "address": "http://localhost:8000/v1"}
    stack ai check --when-ready   # first waits until the server lists its models again
    stack ai check --last         # {"answer": {...}}: the answer kept for this [ai], asks nothing

`--when-ready` is for a server that is starting again (`stack ai key new`
runs it after it restarted oMLX): it waits until the server lists its
models, then asks; a server that does not come back in time keeps
nothing, because it has not failed.

`--last` asks nothing: it reads the answer kept for the current `[ai]`,
or null while nobody asked it. The setup page reads it on every load,
through the host API, to know whether the AI is ready.

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
    from ..answer import check, check_when_ready, said, stored

    ai = config.get("stack", {}).get("ai", {})
    if "--last" in args:
        return {"answer": stored(config["instance_dir"], ai)}
    if not ai.get("openai_url"):
        return {"error": "No AI server is set in stack.toml [ai]. Run './stack up ai', or "
                         "'./stack ai switch <url>' for a server elsewhere."}
    if "--when-ready" in args:
        found = check_when_ready(config["instance_dir"], ai)
        if found is None:
            return {"error": "The AI server did not list its models in time, so nothing "
                             "was asked."}
    else:
        found = check(config["instance_dir"], ai)
    if not found["ok"]:
        return {**found, "error": said(found)}
    print(f"  ✓ {said(found)} at {found['address']}")
    return found
