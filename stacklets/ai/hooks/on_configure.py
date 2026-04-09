"""AI stacklet first-run configuration — choose LLM provider.

Two paths:
  1. External endpoint — user provides an OpenAI-compatible URL + API key.
     We store it and skip oMLX installation entirely.
  2. Managed oMLX — we install and manage oMLX via Homebrew.

Whisper and TTS are always installed regardless.

Runs before on_install. Only fires on first 'stack up ai'.
Skipped when STACK_SETUP_CONFIRMED=1 (installer already confirmed).
"""

import os
import sys
from pathlib import Path

from stack.prompt import section, out, nl, dim, bullet, confirm, ask, done, warn


def _probe_endpoint(url: str, key: str = "") -> bool:
    """Quick check if an OpenAI-compatible endpoint is reachable."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from backend import _probe
    result = _probe(url, key)
    return result.reachable


def _ask_external_endpoint(ctx) -> bool:
    """Prompt for an external endpoint. Returns True if configured."""
    nl()
    out("Enter the URL of your OpenAI-compatible endpoint.")
    dim("Examples: https://api.openai.com/v1, http://192.168.1.50:11434/v1")
    nl()

    url = ask("Endpoint URL")
    if not url:
        return False
    url = url.strip().rstrip("/")
    if not url.startswith("http"):
        url = f"http://{url}"
    if not url.endswith("/v1"):
        url = f"{url}/v1"

    key = ask("API key (leave empty if none)")
    key = key.strip() if key else ""

    if not _probe_endpoint(url, key):
        warn(f"Cannot reach {url}")
        out("Check the URL, make sure the server is running, and try again.")
        nl()
        if confirm("Try again?"):
            return _ask_external_endpoint(ctx)
        return False

    done(f"Connected to {url}")
    ctx.cfg("provider", "external")
    ctx.cfg("openai_url", url)
    ctx.cfg("openai_key", key)
    if key:
        ctx.secret("AI_API_KEY", key)
    return True


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

    # Already configured — nothing to do
    if provider in ("managed", "external"):
        return

    section("AI Engine", "Local AI on your Mac's GPU")
    out("oMLX runs models directly on your GPU via Metal.")
    out("Nothing leaves your network.")
    nl()
    dim("First-time setup downloads ~2.5 GB and takes about 5-10 minutes.")
    dim("After that, everything starts in seconds.")
    nl()

    # ── Provider choice ─────────────────────────────────────────────
    # Y = managed oMLX (recommended, just works)
    # N = bring your own OpenAI-compatible endpoint (advanced, unsupported)
    if confirm("Set up oMLX?", default=True):
        if not _check_brew_available():
            raise RuntimeError("Homebrew not found")

        ctx.cfg("provider", "managed")
        ctx.cfg("openai_url", "http://localhost:42060/v1")
        ctx.cfg("openai_key", "local")
        return

    # ── External endpoint (advanced) ────────────────────────────────
    nl()
    warn("Bring your own endpoint is for advanced users.")
    dim("This is unsupported. Your mileage may vary.")
    nl()

    if _ask_external_endpoint(ctx):
        nl()
        dim("oMLX will be skipped. Whisper and TTS still get set up.")
        nl()
        return

    nl()
    out("No worries, setting up oMLX instead.")
    nl()

    if not _check_brew_available():
        raise RuntimeError("Homebrew not found")

    ctx.cfg("provider", "managed")
    ctx.cfg("openai_url", "http://localhost:42060/v1")
    ctx.cfg("openai_key", "local")
