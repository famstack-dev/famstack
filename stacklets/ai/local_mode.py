"""Ask what installing the ai stacklet does to an AI server set with `switch`.

`stack ai switch <url>` points the stack at a server the stack does not
manage: on another machine, a hosted provider, or an app on this Mac.
Installing the ai stacklet after that, the first `stack up ai`, can mean
two setups. Yes moves chat and voice onto the engine this stacklet
installs. No keeps that server for chat and installs only speech-to-text
and text-to-speech here, which is how a Mac running its own AI app gets
voice. An installed stacklet never asks: its engine is chosen with
`stack ai switch`, and `stack up ai` only starts it.

The question is a setup form (ADR-021), asked by the hook `on_configure`.
"""

from stack.ai.probe import on_this_mac
from stack.forms import Form

# `[ai]` for the engine the stack manages on this Mac, and its Whisper.
MANAGED_ENGINE = {
    "provider": "managed",
    "openai_url": "http://localhost:42060/v1",
    "openai_key": "local",
    "whisper_url": "http://localhost:42062/v1",
    "whisper_key": "",
}


def describe(url: str) -> str:
    """The configured server the way the admin knows it."""
    if on_this_mac(url):
        return f"an AI server on this Mac that the stack does not manage ({url})"
    return f"a remote AI server ({url})"


def engine_form(url: str) -> Form:
    """Whether the engine on this Mac takes over from the server at `url`."""
    return Form(
        id="ai.engine",
        title="AI Engine",
        text=(f"The stack uses {describe(url)}.\n\n"
              "Yes installs the AI engine on this Mac and moves chat and voice to it. "
              "No keeps that server for chat and installs only speech-to-text and "
              "text-to-speech here."),
        schema={
            "type": "object",
            "properties": {
                "use_this_mac": {
                    "type": "boolean",
                    "title": "Use the AI engine on this Mac instead?",
                    "default": False,
                },
            },
            "required": ["use_this_mac"],
        },
    )


def take_over(ctx, use_this_mac: bool) -> None:
    """Switch `[ai]` to the local engine on a yes; keep the server on a no."""
    url = ctx.cfg("openai_url", default="")
    if not use_this_mac:
        ctx.step(f"Chat stays on {url}.")
        return

    for key, value in MANAGED_ENGINE.items():
        ctx.cfg(key, value)

    model = ctx.cfg("default", default="")
    if model:
        ctx.step(f"Default model: {model}. If this Mac does not have it, choose "
                 "one with './stack ai switch managed --model <id>'.")
