"""AI stacklet post-install: voice greeting, backend verification, model check.

Runs after the AI engine containers are up. Three acts:
1. TTS demo, with speech on: the Mac speaks for the first time
2. LLM backend check — verify the configured endpoint is reachable
3. Model check — confirm the default model is available
4. AI intro: the LLM writes a short message, TTS speaks it with speech on

Before them, the house voice's speech model is downloaded, so the
greeting can be spoken.
"""

import sys

from stack.prompt import nl, out, orange, confirm

from ..speech_setting import speech_is_off


def run(ctx):
    interactive = sys.stdin.isatty()

    # ── The speech model ─────────────────────────────────────────────
    # This hook runs before on_start_ready on the first up, so without
    # it the greeting below would ask for a model oMLX does not have yet.
    from ..voices import download_house_voice
    download_house_voice(ctx)

    # ── Voice greeting demo ──────────────────────────────────────────
    # With speech off no voice runs, so there is nothing to hear.
    if interactive and not speech_is_off(ctx.stack.config):
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

    from ..backend import ensure_backend, ensure_model

    result = ensure_backend(ctx.stack.root)
    if "error" in result:
        return

    # ── Check default model is available ─────────────────────────────
    default_model = ctx.cfg("default")
    if default_model:
        ensure_model(ctx.stack.root, default_model)

    # ── AI intro — LLM + TTS together ───────────────────────────────
    if interactive:
        ctx.stack.run_cli_command("ai", "intro")
