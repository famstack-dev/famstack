"""Ask before `stack up ai` moves a remote AI setup onto this Mac.

`stack ai connect <url>` points the stack at a server elsewhere. Bringing
up the ai stacklet after that means the engine and speech-to-text on this
Mac take over, which is a different setup, not a restart. So it is asked,
not assumed, and a "no" leaves the remote endpoint exactly as it was.
"""

import sys

from stack.prompt import confirm, dim, nl, out, warn

LOCAL_URL = "http://localhost:42060/v1"
LOCAL_WHISPER = "http://localhost:42062/v1"


def switch_to_local(ctx) -> None:
    """Switch `[ai]` to the local engine, once the admin has said yes.

    Does nothing unless the provider is a remote endpoint. Raises when
    the answer is no, or when there is no terminal to ask in, so the
    `stack up` stops before anything is installed or started.
    """
    if ctx.cfg("provider", default="") != "external":
        return
    url = ctx.cfg("openai_url", default="")

    nl()
    warn(f"You are connected to a remote AI endpoint ({url}).")
    out("Bringing up the ai stacklet switches the stack to local mode: the")
    out("AI engine and speech-to-text on this Mac take over from it.")
    nl()

    if not sys.stdin.isatty():
        raise RuntimeError(
            f"The stack uses the remote AI endpoint {url}. Bringing up the "
            "ai stacklet switches it to local mode, which needs a yes: run "
            "'./stack up ai' in a terminal.")
    if not confirm("Are you sure?", default=False):
        raise RuntimeError(f"Cancelled. The stack still uses {url}.")

    ctx.cfg("provider", "managed")
    ctx.cfg("openai_url", LOCAL_URL)
    ctx.cfg("openai_key", "local")
    ctx.cfg("whisper_url", LOCAL_WHISPER)
    ctx.cfg("whisper_key", "")

    model = ctx.cfg("default", default="")
    if model:
        dim(f"Default model: {model}. If this Mac does not have it, choose "
            "one with './stack ai connect local --model <id>'.")
    nl()
