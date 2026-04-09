"""AI stacklet post-install: voice greeting, backend verification, model check.

Runs after the AI engine containers are up. Three acts:
1. TTS demo — the Mac speaks for the first time (wow moment)
2. LLM backend check — verify the configured endpoint is reachable
3. Model check — confirm the default model is available
4. AI intro — the LLM writes a short message, TTS speaks it
"""

import sys
from pathlib import Path

from stack.prompt import nl, out, orange, confirm


def run(ctx):
    sys.path.insert(0, str(Path(__file__).parent.parent))
    interactive = sys.stdin.isatty()
    provider = ctx.cfg("provider", default="managed")

    # ── Voice greeting demo ──────────────────────────────────────────
    if interactive:
        nl()
        orange("Your Mac learned to speak. Want to hear it?")
        out("Turn up your volume!")
        nl()

        if confirm("Let's go?"):
            while True:
                ctx.stack.run_cli_command("ai", "hello")
                nl()
                if confirm("Did you hear it?", default=True):
                    break
                from stack.prompt import warn, dim
                warn("Check that your volume is up and speakers are connected.")
                dim("You can replay it anytime with './stack ai hello'.")
                nl()
                if not confirm("Try again?"):
                    break

    # ── Verify LLM backend ───────────────────────────────────────────
    if interactive:
        nl()
        from stack.prompt import section
        section("Brain", "Checking your language model")

    from backend import ensure_backend, ensure_model

    result = ensure_backend(ctx.stack.root)
    if "error" in result:
        return

    # ── Check default model is available ─────────────────────────────
    default_model = ctx.cfg("default")
    if default_model:
        ensure_model(ctx.stack.root, default_model)

    # ── Disable thinking for Qwen3.5 on managed oMLX ────────────────
    if provider == "managed" and default_model:
        from thinking import is_qwen35, disable_thinking
        from omlx import OMLXClient, is_omlx
        from stack.prompt import dim

        url = result.get("url", "")
        key = result.get("key", "")
        admin_url = url.rstrip("/").replace("/v1", "")
        if is_qwen35(default_model) and is_omlx(admin_url):
            client = OMLXClient(admin_url, api_key=key)
            if client.login():
                disable_thinking(client, default_model, log=lambda msg: dim(f"  {msg}"))

    # ── AI intro — LLM + TTS together ───────────────────────────────
    if interactive:
        ctx.stack.run_cli_command("ai", "intro")
