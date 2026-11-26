"""AI stacklet start hook — validate config, start native services.

Runs on every `stack up ai`. Validates that the AI provider is
configured before proceeding, and refuses a start while `[ai]` says the
stack runs without an AI (`provider = "none"`, `off.py`), naming the two routes. This is the on_start contract:
check required config first, raise with a clear fix if missing.
For the managed oMLX it starts famstack's own oMLX (`omlx_start.running`,
the one start the preparation uses too), which keeps its settings in line
with the stack and restarts it when they changed. It installs and
downloads nothing: `stack up ai` prepares first (`prepare.py`), before
its turn. Whisper's agent is written and loaded again too. Both go through one lifecycle
(`host_services`), which `on_stop` reverses. An AI server the stack
does not manage, such as the oMLX app, is never changed. Speech off (`[ai] speech = false` or
`--no-voice`) leaves the Whisper service alone.
"""

from pathlib import Path

from stack.prompt import out, nl, warn, dim, TEAL, RESET

from .. import off, old_speech, omlx_app, whisper_service
from ..omlx_start import running
from ..speech_setting import speech_is_off

__all__ = ["run"]


def run(ctx):
    """Start what `[ai]` names on this Mac: famstack's oMLX for the
    managed route, then Whisper when it is built. Raises with the fix for
    a missing or contradicting `[ai]`, and while the stack runs no AI."""
    old_speech.remove(ctx)

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

    # No AI for now: a bare start has nothing to run; a route turns it on.
    off.refuse(ctx.stack.config)

    if provider == "managed":
        # Never beside the oMLX app: two MLX servers on one graphics chip
        # can crash the Mac (`omlx_app`). Stops before anything changes.
        omlx_app.refuse_while_running()

    if provider == "external":
        url = ctx.cfg("openai_url", default="")
        if not url:
            nl()
            warn("External provider selected but no endpoint URL set.")
            out("Set [ai].openai_url in stack.toml, or reconfigure:")
            nl()
            out(f"  {TEAL}stack destroy ai && stack up ai{RESET}")
            nl()
            raise RuntimeError("Missing openai_url for external provider")
        # The engine is chosen with `stack ai switch`, never by starting
        # the stacklet: text elsewhere with voice on this Mac is a setup
        # an admin chose, and `up` only starts what the stacklet runs.
        dim(f"Chat uses the AI server at {url}. "
            "'./stack ai switch managed' moves it to the engine the stack runs.")

    if provider == "managed":
        _start_local_engine(ctx)

    _reconcile_whisper(ctx)


def _start_local_engine(ctx):
    """Start famstack's oMLX (`omlx_start.running`): launchd loads it when
    it does not have it, and restarts it when a setting changed or it
    refuses the key, because oMLX reads its settings at start only. A
    running oMLX with nothing changed is left as it is. A failed start
    takes back only what it started. An instance an earlier release set
    up with Homebrew's service has moved off it in the preparation
    (`homebrew_service.move`)."""
    with running(ctx):
        pass


def _reconcile_whisper(ctx):
    """Start Whisper's LaunchAgent as the current code writes it.

    The install builds Whisper once; its agent is written again on every
    start (`host_services.start`, by content): identical on disk means no
    write and no restart, changed means rewrite and restart, and an agent
    `stack down ai` removed is written and loaded again. A plain
    `stack restart ai` is thereby enough to deploy a flag change.

    With speech off it neither writes nor loads the service. A Whisper
    that already runs keeps running: turning speech off stops nothing.
    """
    # A context without a Stack (an embedding) has no paths to read.
    if getattr(ctx, "stack", None) is None or speech_is_off(_config(ctx)):
        return
    data_dir = Path(ctx.stack.data)
    whisper_bin = whisper_service.server(data_dir)
    model_path = whisper_service.model_file(data_dir)
    if not whisper_bin.exists() or not model_path.exists():
        return  # whisper never installed here (or opted out): nothing to start
    whisper_service.start(ctx, data_dir, whisper_bin, model_path)


def _config(ctx) -> dict:
    """The parsed stack.toml, or nothing from a context without a Stack
    (the start-guard tests, an embedding), which then gets the defaults."""
    stack = getattr(ctx, "stack", None)
    return stack.config if stack is not None else {}
