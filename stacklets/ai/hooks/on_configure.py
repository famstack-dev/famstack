"""AI stacklet first-run configuration: the two routes to an AI.

The first `stack up ai` asks the same two routes as the setup page:

    Set up oMLX?   yes ──▶ famstack installs oMLX (Homebrew) and runs it
                   no  ──▶ the address and key of your own AI server,
                           checked and stored as `stack ai switch` does

Once `[ai]` names a server, managed or not, it asks nothing: the engine
is chosen with `stack ai switch`, and `stack up ai` only installs and
starts what the stacklet runs. No server on this Mac is probed, and
nothing on a server famstack does not run is changed.

All questions are setup forms (ADR-021): a terminal asks them, and
`--answer setup_omlx=no --answer address=<url> --answer api_key=<key>`
answers them without one. A server that refuses the key or does not
answer stores nothing, and the next start asks again.
"""

import shutil

__all__ = ["questions", "apply"]

from stack.forms import Form

from ..omlx import managed_key

# Route 2's address starts with the oMLX app's default address.
SERVER_ADDRESS = "http://localhost:8000/v1"

SETUP_OMLX = Form(
    id="ai.omlx",
    title="AI Engine",
    text=("Yes: famstack installs oMLX on this Mac and looks after its models. It runs them "
          "on your GPU via Metal, and nothing leaves your network. The first start downloads "
          "the models and can take 20 to 30 minutes. famstack is tested and tuned with oMLX, "
          "and we recommend it.\n\n"
          "No: use your own AI server, any server that speaks the OpenAI API, for example the "
          "oMLX app or a server on another computer."),
    schema={
        "type": "object",
        "properties": {
            "setup_omlx": {"type": "boolean", "title": "Set up oMLX?", "default": True},
        },
        "required": ["setup_omlx"],
    },
)

SERVER = Form(
    id="ai.server",
    title="Your own AI server",
    text=("famstack checks that the server answers, picks its chat model and asks it one short "
          "question. It changes nothing on the server."),
    schema={
        "type": "object",
        "properties": {
            "address": {"type": "string", "title": "Address", "default": SERVER_ADDRESS,
                        "minLength": 1},
            "api_key": {"type": "string", "title": "API key (empty when it needs none)",
                        "writeOnly": True},
        },
        "required": ["address"],
    },
)

NO_HOMEBREW = (
    "Homebrew is required to install oMLX. Install it with "
    '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)", '
    "then run './stack up ai' again.")


def questions(ctx, answers):
    """The next open setup form of the first `stack up ai`, or None.

    None for an installed stacklet, or once `[ai]` names a server. After
    a no to oMLX, the server's address and key. Reads, never writes, and
    asks no server.
    """
    if ctx.cfg("provider", default="") or not ctx.stack._is_first_run(ctx.stacklet_id):
        return None
    if form := SETUP_OMLX.pending(answers):
        return form
    return None if answers["setup_omlx"] else SERVER.pending(answers)


def apply(ctx, answers):
    """Write `[ai]` as the answers decide; raise when nothing may be set up.

    Yes: `[ai]` for the oMLX the stack runs, which `on_install` installs.
    No: the server goes through `stack ai switch`, which checks it, asks
    it one question and writes `[ai]`; its refusal is raised as it is.
    """
    if not answers["setup_omlx"]:
        _use_your_server(ctx, answers["address"], answers.get("api_key", ""))
        return
    if not shutil.which("brew"):
        raise RuntimeError(NO_HOMEBREW)

    ctx.cfg("provider", "managed")
    ctx.cfg("openai_url", "http://localhost:42060/v1")
    # The placeholder; the install then gives oMLX and the stack a
    # generated key (`omlx.py`).
    ctx.cfg("openai_key", managed_key(ctx.stack.data))


def _use_your_server(ctx, address: str, key: str) -> None:
    """Route 2: `stack ai switch <address> --key <key>`, in this process,
    so the key never shows on a command line. Raises with the switch's
    error when it wrote nothing."""
    switched = ctx.stack.run_cli_command("ai", "switch", [address, "--key", key])
    if "error" in switched:
        raise RuntimeError(switched["error"])
