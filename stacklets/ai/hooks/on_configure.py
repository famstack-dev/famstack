"""AI stacklet first-run configuration: confirm the local AI engine.

`stack up ai` installs and manages oMLX, Whisper and TTS on this Mac.
An AI server elsewhere is `stack ai switch <url>`, which installs
nothing here; after that, installing this stacklet asks whether the
engine here takes over or only voice is installed (`local_mode`). Once
installed, the engine is chosen with `stack ai switch`, and `stack up ai`
only starts it, so there is nothing to ask.

Before it offers oMLX, a fresh instance looks for an AI server already
running on this Mac, such as the oMLX app, and offers that one instead
(`running_server`): a second engine next to it would hold a second model
in memory.
Once the stack uses that server (`[ai] openai_url` is its address), the
first `stack up ai` asks nothing more and installs only what the stack
needs beside it.

All questions are setup forms (ADR-021): a terminal asks them, and
`--answer setup_omlx=yes`, `--answer use_running_server=yes` or
`--answer use_this_mac=no` answers them without one. A no to oMLX
installs nothing (`Cancelled`).
"""

import shutil

__all__ = ["questions", "apply"]

from stack.forms import Form
from stack.hooks import Cancelled

from .. import running_server
from ..local_mode import engine_form, take_over

# How the admin points the stack at an AI server it does not install: the
# oMLX app, or a server on another machine. `switch` checks that the server
# answers, and says so when it wants a key that was not given.
SWITCH = "./stack ai switch <its address> --key <its API key>"

SETUP_OMLX = Form(
    id="ai.omlx",
    title="AI Engine",
    text=("oMLX runs models directly on your GPU via Metal. Nothing leaves your network.\n\n"
          "The first start downloads the models and can take 20 to 30 minutes. "
          "After that, everything starts in seconds.\n\n"
          "Already running oMLX or another AI server? Answer no, then run "
          f"'{SWITCH}'."),
    schema={
        "type": "object",
        "properties": {
            "setup_omlx": {"type": "boolean", "title": "Set up oMLX?", "default": True},
        },
        "required": ["setup_omlx"],
    },
)

NO_HOMEBREW = (
    "Homebrew is required to install oMLX. Install it with "
    '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)", '
    "then run 'stack up ai' again.")


def questions(ctx, answers):
    """The next open setup form of the first `stack up ai`, or None.

    None for an installed stacklet or a provider already managed here.
    After `stack ai switch`: whether the engine here takes over. On a
    fresh instance: the AI server running on this Mac when one answers,
    then oMLX. Reads and probes, never writes.
    """
    provider = ctx.cfg("provider", default="")
    if provider == "managed" or not ctx.stack._is_first_run(ctx.stacklet_id):
        return None
    # A server set with `stack ai switch`: the admin says whether the
    # engine here takes over, or only the voice services are installed.
    # Not for the AI server found running on this Mac (the oMLX app, as
    # the setup page connects it): a second engine next to it would hold
    # a second model in memory, so there is nothing to choose.
    if provider == "external":
        url = ctx.cfg("openai_url", default="")
        if url in running_server.RUNNING_SERVERS:
            return None
        return engine_form(url).pending(answers)
    url = running_server.running_server()
    if url:
        if form := running_server.use_form(url).pending(answers):
            return form
        if answers["use_running_server"]:
            if running_server.needs_key(url):
                return running_server.key_form(url).pending(answers)
            return None
    return SETUP_OMLX.pending(answers)


def apply(ctx, answers):
    """Write `[ai]` as the answers decide; raise when nothing may be set up."""
    if "use_this_mac" in answers:
        take_over(ctx, answers["use_this_mac"])
        return
    if answers.get("use_running_server"):
        url = running_server.running_server()
        if not url:
            raise RuntimeError("The AI server on this Mac stopped answering. "
                               "Start it, then run 'stack up ai' again.")
        running_server.use(ctx, url, answers.get("api_key", ""))
        return

    # `stack up ai` is the local engine. A server elsewhere is
    # `stack ai switch`, which installs nothing on this Mac.
    if not answers["setup_omlx"]:
        raise Cancelled(
            "Cancelled. The local AI engine was not set up. To use oMLX or another AI "
            f"server you already run, or a hosted provider, run '{SWITCH}' instead.")
    if not shutil.which("brew"):
        raise RuntimeError(NO_HOMEBREW)

    ctx.cfg("provider", "managed")
    ctx.cfg("openai_url", "http://localhost:42060/v1")
    ctx.cfg("openai_key", "local")
