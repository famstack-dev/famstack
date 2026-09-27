"""Ask what installing the ai stacklet does to an AI server set with `connect`.

`stack ai connect <url>` points the stack at a server the stack does not
manage: on another machine, a hosted provider, or an app on this Mac.
Installing the ai stacklet after that, the first `stack up ai`, can mean
two setups. Yes moves chat and voice onto the engine this stacklet
installs. No keeps that server for chat and installs only speech-to-text
and text-to-speech here, which is how a Mac running its own AI app gets
voice. An installed stacklet never asks: its engine is chosen with
`stack ai connect`, and `stack up ai` only starts it.
"""

from stack.ai.probe import on_this_mac
from stack.prompt import confirm, dim, nl, out, warn

LOCAL_URL = "http://localhost:42060/v1"
LOCAL_WHISPER = "http://localhost:42062/v1"


def describe(url: str) -> str:
    """The configured server the way the admin knows it."""
    if on_this_mac(url):
        return f"an AI server on this Mac that the stack does not manage ({url})"
    return f"a remote AI server ({url})"


def choose_engine(ctx) -> None:
    """Switch `[ai]` to the local engine on a yes; keep the server on a no.

    Does nothing unless the provider is a server set with `connect`. The
    caller has made sure there is a terminal to ask in.
    """
    if ctx.cfg("provider", default="") != "external":
        return
    url = ctx.cfg("openai_url", default="")

    nl()
    warn(f"The stack uses {describe(url)}.")
    out("Yes installs the AI engine on this Mac and moves chat and voice to it.")
    out("No keeps that server for chat and installs only speech-to-text and")
    out("text-to-speech here.")
    nl()

    if not confirm("Use the AI engine on this Mac instead?", default=False):
        dim(f"Chat stays on {url}.")
        nl()
        return

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
