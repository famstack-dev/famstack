"""AI stacklet first-run configuration: confirm the local AI engine.

`stack up ai` installs and manages oMLX, Whisper and TTS on this Mac.
An AI server elsewhere is `stack ai switch <url>`, which installs
nothing here. Once `[ai]` names a server, managed or not, the first
`stack up ai` asks nothing: the engine is chosen with `stack ai switch`,
and `stack up ai` only installs and starts what the stacklet runs. No
server on this Mac is probed.

The question is a setup form (ADR-021): a terminal asks it, and
`--answer setup_omlx=yes` answers it without one. A no to oMLX installs
nothing (`Cancelled`).
"""

import shutil

__all__ = ["questions", "apply"]

from stack.forms import Form
from stack.hooks import Cancelled

from ..omlx import managed_key

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
    "then run './stack up ai' again.")


def questions(ctx, answers):
    """The next open setup form of the first `stack up ai`, or None.

    None for an installed stacklet, or once `[ai]` names a server. Reads,
    never writes, and asks no server.
    """
    if ctx.cfg("provider", default="") or not ctx.stack._is_first_run(ctx.stacklet_id):
        return None
    return SETUP_OMLX.pending(answers)


def apply(ctx, answers):
    """Write `[ai]` as the answers decide; raise when nothing may be set up."""
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
    # The key oMLX already has, or the placeholder; the install then
    # gives oMLX and the stack a generated one (`omlx.py`).
    ctx.cfg("openai_key", managed_key())
