"""AI stacklet first-run configuration: confirm the local AI engine.

`stack up ai` installs and manages oMLX, Whisper and TTS on this Mac.
An AI server elsewhere is `stack ai switch <url>`, which installs
nothing here; after that, installing this stacklet asks whether the
engine here takes over or only voice is installed (`local_mode`). Once
installed, the engine is chosen with `stack ai switch`, and `stack up ai`
only starts it, so there is nothing to ask.

Both questions are setup forms (ADR-021): a terminal asks them, and
`--answer setup_omlx=yes` or `--answer use_this_mac=no` answers them
without one. A no to oMLX installs nothing (`Cancelled`).
"""

import shutil

from stack.forms import Form
from stack.hooks import Cancelled

from ..local_mode import engine_form, take_over

SETUP_OMLX = Form(
    id="ai.omlx",
    title="AI Engine",
    text=("oMLX runs models directly on your GPU via Metal. Nothing leaves your network.\n\n"
          "First-time setup downloads about 2.5 GB and takes about 5 to 10 minutes. "
          "After that, everything starts in seconds."),
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
    provider = ctx.cfg("provider", default="")
    if provider == "managed" or not ctx.stack._is_first_run(ctx.stacklet_id):
        return None
    # A server set with `stack ai switch`: the admin says whether the
    # engine here takes over, or only the voice services are installed.
    if provider == "external":
        return engine_form(ctx.cfg("openai_url", default="")).pending(answers)
    return SETUP_OMLX.pending(answers)


def apply(ctx, answers):
    if "use_this_mac" in answers:
        take_over(ctx, answers["use_this_mac"])
        return

    # `stack up ai` is the local engine. A server elsewhere is
    # `stack ai switch`, which installs nothing on this Mac.
    if not answers["setup_omlx"]:
        raise Cancelled(
            "Cancelled. The local AI engine was not set up. To use an AI server on "
            "another machine or a hosted provider, run './stack ai switch <url>' instead.")
    if not shutil.which("brew"):
        raise RuntimeError(NO_HOMEBREW)

    ctx.cfg("provider", "managed")
    ctx.cfg("openai_url", "http://localhost:42060/v1")
    ctx.cfg("openai_key", "local")
