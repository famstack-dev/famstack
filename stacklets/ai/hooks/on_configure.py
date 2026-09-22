"""AI stacklet first-run configuration — confirm the local AI engine.

`stack up ai` installs and manages oMLX, Whisper and TTS on this Mac.
An AI server elsewhere is `stack ai connect <url>`, which installs
nothing here; after that, bringing up this stacklet asks before it
switches the stack back to local mode.

Runs before on_install. Only fires on first 'stack up ai'.
Skipped when STACK_SETUP_CONFIRMED=1 (installer already confirmed).
"""

import os
import sys
from pathlib import Path

from stack.prompt import section, out, nl, dim, confirm, warn


def _check_brew_available():
    """Check if Homebrew is available. Guide user to install if not."""
    import shutil
    if shutil.which("brew"):
        return True

    nl()
    warn("Homebrew is required to install oMLX.")
    out("Install it with:")
    nl()
    out('  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
    nl()
    out("After installing Homebrew, run:")
    nl()
    out("  stack up ai")
    nl()
    return False


def run(ctx):
    if os.environ.get("STACK_SETUP_CONFIRMED") == "1":
        return

    if not sys.stdin.isatty():
        raise RuntimeError(
            "'ai' requires interactive setup (installs native software). "
            "Run this command in a terminal."
        )

    provider = ctx.cfg("provider", default="")

    # A remote endpoint set with `stack ai connect`: installing the ai
    # stacklet replaces it with the local engine, so that needs a yes.
    if provider == "external":
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from local_mode import switch_to_local
        switch_to_local(ctx)
        return

    # Already configured — nothing to do
    if provider == "managed":
        return

    section("AI Engine", "Local AI on your Mac's GPU")
    out("oMLX runs models directly on your GPU via Metal.")
    out("Nothing leaves your network.")
    nl()
    dim("First-time setup downloads ~2.5 GB and takes about 5-10 minutes.")
    dim("After that, everything starts in seconds.")
    nl()

    # `stack up ai` is the local engine. A server elsewhere is
    # `stack ai connect`, which installs nothing on this Mac.
    if not confirm("Set up oMLX?", default=True):
        nl()
        out("To use an AI server on another machine or a hosted provider,")
        out("run './stack ai connect <url>' instead. Nothing was installed.")
        nl()
        raise RuntimeError("Cancelled: the local AI engine was not set up")

    if not _check_brew_available():
        raise RuntimeError("Homebrew not found")

    ctx.cfg("provider", "managed")
    ctx.cfg("openai_url", "http://localhost:42060/v1")
    ctx.cfg("openai_key", "local")
