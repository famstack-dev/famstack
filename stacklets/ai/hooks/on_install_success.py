"""AI stacklet post-install: voice greeting, backend check, AI intro.

Runs once, after the first `stack up ai` started the AI. Three acts:
1. TTS demo, with speech on: the Mac speaks for the first time
2. LLM backend check: the configured endpoint is reachable, and on a
   server the stack does not run, a warning when `[ai] default` is missing
3. AI intro: the LLM writes a short message, TTS speaks it with speech on

It downloads nothing. The preparation (`prepare.py`) fetched the
language model and the house voice before the start took its turn, so
the greeting has its voice.
"""

import sys

from stack.prompt import nl, out, orange, confirm

from ..speech_setting import speech_is_off


def run(ctx):
    interactive = sys.stdin.isatty()

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

    # ── The default model on a server the stack does not run ─────────
    # The preparation fetched it onto famstack's oMLX; on another server
    # the admin loads it, and this says when it is missing there.
    if ctx.cfg("provider", default="") != "managed" and (model := ctx.cfg("default")):
        ensure_model(ctx.stack.root, model)

    # ── AI intro — LLM + TTS together ───────────────────────────────
    if interactive:
        ctx.stack.run_cli_command("ai", "intro")
