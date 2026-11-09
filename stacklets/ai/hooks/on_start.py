"""AI stacklet start hook — validate config, start native services.

Runs on every `stack up ai`. Validates that the AI provider is
configured before proceeding. This is the on_start contract:
check required config first, raise with a clear fix if missing.
For the managed oMLX it also keeps oMLX's settings in line with the
stack (its address, and its memory on a 16 GB Mac) and restarts
it when they changed.
"""

import os
import shutil
import subprocess
from pathlib import Path

from stack.prompt import out, nl, warn, dim, TEAL, RESET

# on_stop reads the markers here to know what famstack manages.
STATE_DIR = Path(__file__).resolve().parent.parent / ".state"


# The speech container this stacklet ran before the stack's voices moved
# to oMLX (Piper, through openedai-speech). An instance that ran it keeps
# it, and its image, after the update, and nothing else uses either.
OLD_SPEECH_CONTAINER = "stack-ai-speech"


def run(ctx):
    _remove_old_speech_container(ctx)

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
        # The stack manages the engine, so a missing one is installed:
        # after `stack ai switch managed` on a Mac that has only the voice
        # services, or when a Python upgrade, a brew cleanup or a migrated
        # machine took the binary away after setup.
        _install_hook().install_omlx(ctx, STATE_DIR)

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
    loosened = _fit_omlx_to_a_small_mac()

    url = ctx.cfg("openai_url", default="") or "http://localhost:42060/v1"
    if probe(url, ctx.cfg("openai_key", default="")).reachable:
        if moved or loosened:
            # oMLX reads its settings at start only.
            ctx.step("Restarting oMLX with its new settings")
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


# A Mac with this much memory or less runs oMLX with two settings that let
# the default model work beside Docker's VM, Whisper and macOS:
#
#   - The "aggressive" memory guard. oMLX refuses a model that does not
#     fit under the guard's ceiling, and its default, "balanced", computes
#     it from the memory free at that moment: on a 16 GB Mac that was 4.4
#     to 5.3 GB, under the 5.8 GB of the default model, so every chat
#     request was refused. "aggressive" raised the ceiling to 7.4 GB.
#   - Chunked prefill. Reading a document's prompt in one piece took the
#     process from 5.8 to 7.0 GB at 2,000 tokens, over the guard's hard
#     limit, so oMLX aborted the request and unloaded the model. Read in
#     chunks, a 5,000-token prompt stayed under it.
SMALL_MAC_GB = 16
# oMLX's own default; a tier the admin chose (safe, custom) stays.
DEFAULT_MEMORY_GUARD = "balanced"


def _fit_omlx_to_a_small_mac() -> bool:
    """Set oMLX's memory guard to "aggressive" (when it is at oMLX's
    default) and turn on chunked prefill, on a Mac with `SMALL_MAC_GB` or
    less. Returns whether the settings changed. on_install writes the file
    before the first start; without it there is nothing to reconcile yet."""
    import json

    path = Path.home() / ".omlx" / "settings.json"
    if not path.exists() or _ram_gb() > SMALL_MAC_GB:
        return False
    settings = json.loads(path.read_text())
    before = json.dumps(settings, sort_keys=True)
    memory = settings.setdefault("memory", {})
    if memory.get("memory_guard_tier", DEFAULT_MEMORY_GUARD) == DEFAULT_MEMORY_GUARD:
        memory["memory_guard_tier"] = "aggressive"
    settings.setdefault("scheduler", {})["chunked_prefill"] = True
    if json.dumps(settings, sort_keys=True) == before:
        return False
    path.write_text(json.dumps(settings, indent=2))
    return True


def _ram_gb() -> float:
    """The Mac's memory in GB, or infinity when macOS does not say: an
    unknown Mac keeps oMLX's default."""
    try:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True,
                             timeout=5).stdout
        return int(out.strip()) / 1024 ** 3
    except (OSError, ValueError, subprocess.SubprocessError):
        return float("inf")


def _remove_old_speech_container(ctx):
    """Remove the old speech container and its image, once, when Docker
    says the container belongs to this stacklet's compose project."""
    from stack import docker

    if not any(c["name"] == OLD_SPEECH_CONTAINER for c in docker.containers_for("ai")):
        return
    ctx.step("Removing the old speech container")
    image = docker.remove_container(OLD_SPEECH_CONTAINER)
    if image and not docker.remove_image(image):
        dim(f"Its image {image} stays: Docker did not remove it.")


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
    mod = _install_hook()
    data_dir = Path(ctx.stack.data)
    whisper_bin = data_dir / "ai" / "whisper.cpp" / "build" / "bin" / "whisper-server"
    model_path = data_dir / "ai" / "whisper-models" / mod.WHISPER_MODEL
    if not whisper_bin.exists() or not model_path.exists():
        return  # whisper never installed here (or opted out) — nothing to reconcile
    state_dir = Path(__file__).resolve().parent.parent / ".state"
    mod._setup_whisper_launchd(ctx, data_dir, whisper_bin, model_path, state_dir)


def _install_hook():
    """The install hook, for the parts of setup a start repeats."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "hook.ai_on_install", Path(__file__).resolve().parent / "on_install.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
