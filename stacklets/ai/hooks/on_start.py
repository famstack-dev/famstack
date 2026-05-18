"""AI stacklet start hook — validate config, start native services.

Runs on every `stack up ai`. Validates that the AI provider is
configured before proceeding. This is the on_start contract:
check required config first, raise with a clear fix if missing.
"""

import os

from stack.prompt import out, nl, warn, dim, TEAL, RESET


def run(ctx):
    # Local-dev opt-out: clear the "voice" compose profile so docker compose
    # skips the Piper TTS container on this `stack up`. Paired with the
    # on_install gate that skips the Whisper build. ctx.env is the same dict
    # that the CLI then hands to compose_up, so mutating it here is enough.
    if os.environ.get("STACK_AI_NO_VOICE") == "1":
        ctx.env["COMPOSE_PROFILES"] = ""
        dim("STACK_AI_NO_VOICE=1 — speech container disabled")

    provider = ctx.cfg("provider", default="")

    if not provider:
        nl()
        warn("AI provider not configured.")
        out("Run the following to set it up:")
        nl()
        out(f"  {TEAL}stack destroy ai{RESET}    (removes setup marker)")
        out(f"  {TEAL}stack up ai{RESET}         (re-runs configuration)")
        nl()
        raise RuntimeError("AI provider not configured")

    if provider == "external":
        url = ctx.cfg("openai_url", default="")
        if not url:
            nl()
            warn("External provider selected but no endpoint URL set.")
            out(f"Set [ai].openai_url in stack.toml, or reconfigure:")
            nl()
            out(f"  {TEAL}stack destroy ai && stack up ai{RESET}")
            nl()
            raise RuntimeError("Missing openai_url for external provider")
