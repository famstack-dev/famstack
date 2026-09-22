"""AI stacklet start hook — validate config, start native services.

Runs on every `stack up ai`. Validates that the AI provider is
configured before proceeding. This is the on_start contract:
check required config first, raise with a clear fix if missing.
"""

import os
import shutil
import sys
from pathlib import Path

from stack.prompt import out, nl, warn, dim, TEAL, RESET

# on_stop reads the markers here to know what famstack manages.
STATE_DIR = Path(__file__).resolve().parent.parent / ".state"


def run(ctx):
    # Local-dev opt-out: clear the "voice" compose profile so docker compose
    # skips the Piper TTS container on this `stack up`. Paired with the
    # on_install gate that skips the Whisper build. ctx.env is the same dict
    # that the CLI then hands to compose_up, so mutating it here is enough.
    if os.environ.get("STACK_AI_NO_VOICE") == "1":
        ctx.env["COMPOSE_PROFILES"] = ""
        dim("STACK_AI_NO_VOICE=1 — speech container disabled")

    # A remote endpoint is only replaced by the local engine with a yes.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from local_mode import switch_to_local
    switch_to_local(ctx)

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

    if provider == "managed" and shutil.which("omlx") is None:
        # Setup ran once and left a marker, so the install hook will not run
        # again on its own. If the binary has since gone -- a Python upgrade
        # that broke its virtualenv, a brew cleanup, a migrated machine --
        # nothing notices: the containers start, `stack up` reports success,
        # and the only symptom is an LLM health check failing with no stated
        # cause. Say what is actually wrong and how to undo it, using the
        # same two-command recovery this hook already teaches above.
        nl()
        warn("oMLX is not installed (no `omlx` command found).")
        out("The AI engine was set up before but is no longer on this Mac.")
        out("Reinstall it with:")
        nl()
        out(f"  {TEAL}stack destroy ai{RESET}    (keeps your downloaded models)")
        out(f"  {TEAL}stack up ai{RESET}         (reinstalls oMLX)")
        nl()
        raise RuntimeError("oMLX missing for managed provider")

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

    if provider == "managed":
        _start_local_engine(ctx)

    _reconcile_whisper(ctx)


def _start_local_engine(ctx):
    """Start oMLX when it is not answering.

    It runs as a Homebrew service that on_install starts once. After
    that nothing started it again, so a service stopped by hand, by a
    brew upgrade or by a crash stayed down while `stack up ai` reported
    the AI as running. `brew services start` is idempotent, and the LLM
    health check that follows waits for the engine to come up.
    """
    from stack.ai.probe import probe

    # famstack manages oMLX whenever it is the provider, so `stack down ai`
    # stops it. on_install records this too, but only when it installed
    # oMLX itself, which an instance first set up remotely never did.
    STATE_DIR.mkdir(exist_ok=True)
    (STATE_DIR / "omlx-managed").touch()

    moved = _bind_omlx(ctx)

    url = ctx.cfg("openai_url", default="") or "http://localhost:42060/v1"
    if probe(url, ctx.cfg("openai_key", default="")).reachable:
        if moved:
            # oMLX reads its settings at start only.
            ctx.step("Restarting oMLX on its new address")
            ctx.shell("brew services restart omlx")
        return
    ctx.step("Starting oMLX")
    ctx.shell("brew services start omlx")


def _bind_omlx(ctx) -> bool:
    """Make oMLX listen where the framework binds every service.

    All interfaces in port mode, so the household's other machines can
    use this Mac's engine, loopback in domain mode. famstack never set
    it, so oMLX kept its own default, loopback, whatever the mode.
    on_install creates the settings file; without it there is nothing
    to reconcile yet. Returns whether the address changed.
    """
    import json

    path = Path.home() / ".omlx" / "settings.json"
    if not path.exists():
        return False
    host = ctx.env.get("PORT_BIND_IP") or "127.0.0.1"
    settings = json.loads(path.read_text())
    if settings.get("server", {}).get("host") == host:
        return False
    settings.setdefault("server", {})["host"] = host
    path.write_text(json.dumps(settings, indent=2))
    return True


def _reconcile_whisper(ctx):
    """Bring the whisper LaunchAgent in line with the current code.

    The plist is generated by on_install, which only runs once — so a
    change to the whisper flags (language pinning, --max-context,
    --suppress-nst) would otherwise never reach an existing install.
    This re-derives the plist on every start and lets the content-
    idempotent setup decide: identical on disk means no write and no
    service bounce, changed means rewrite and reload. A plain
    `stack restart ai` is thereby enough to deploy a config change.
    """
    if os.environ.get("STACK_AI_NO_VOICE") == "1":
        return
    # Best-effort: reconciliation needs the real Stack for paths and
    # config. A context without one (the start-guard tests, an exotic
    # embedding) simply skips it — config validation above is this
    # hook's contract, keeping the plist current is a courtesy.
    if getattr(ctx, "stack", None) is None:
        return
    import importlib.util
    from pathlib import Path

    hooks_dir = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        "hook.ai_on_install", hooks_dir / "on_install.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    data_dir = Path(ctx.stack.data)
    whisper_bin = data_dir / "ai" / "whisper.cpp" / "build" / "bin" / "whisper-server"
    model_path = data_dir / "ai" / "whisper-models" / mod.WHISPER_MODEL
    if not whisper_bin.exists() or not model_path.exists():
        return  # whisper never installed here (or opted out) — nothing to reconcile
    state_dir = Path(__file__).resolve().parent.parent / ".state"
    mod._setup_whisper_launchd(ctx, data_dir, whisper_bin, model_path, state_dir)
