"""AI stacklet start hook — validate config, start native services.

Runs on every `stack up ai`. Validates that the AI provider is
configured before proceeding. This is the on_start contract:
check required config first, raise with a clear fix if missing.
For the managed oMLX it runs famstack's own oMLX in its own folder
(`omlx_service`), keeps its settings in line with the stack (its port,
address and key, and its memory and idle unload on a small Mac,
`omlx_fit`) and restarts it when they changed. An AI server the stack
does not manage, such as the oMLX app, is never changed. Speech off (`[ai] speech = false` or
`--no-voice`) leaves the Whisper service alone.
"""

import shutil
from pathlib import Path

from stack.prompt import out, nl, warn, dim, TEAL, RESET
from stack.profile import profile_of

from .. import homebrew_omlx, omlx_app, omlx_service
from ..omlx import PLACEHOLDER_KEY
from ..omlx_fit import fit_settings_file
from ..speech_setting import speech_is_off

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
    """Run famstack's oMLX in its own folder (`omlx_service`) with the
    stack's settings: port 42060, the framework's bind address (all
    interfaces in port mode, loopback in domain mode), the shared models
    and its key, and on a small Mac the memory profile's settings
    (`omlx_fit`). launchd loads it when it does not have it, and restarts
    it when a setting changed, because oMLX reads them at start only. A
    running oMLX with nothing changed is left as it is.

    An instance set up by an earlier release ran famstack's oMLX as a
    Homebrew service, with `~/.omlx`. Its first start here stops that
    service once and carries the key `[ai]` sends into famstack's folder,
    so apps that use the key keep working.
    """
    # Never beside the oMLX app: two MLX servers on one graphics chip can
    # crash the Mac. Stops the run before anything changes (`omlx_app`).
    omlx_app.refuse_while_running()
    data_dir = Path(ctx.stack.data)
    _leave_the_homebrew_service(ctx)
    carried = None
    if not omlx_service.settings_file(data_dir).exists():
        carried = ctx.cfg("openai_key", default="") or PLACEHOLDER_KEY
    host = ctx.env.get("PORT_BIND_IP") or "127.0.0.1"
    changed = omlx_service.configure(data_dir, host=host, key=carried)
    loosened = fit_settings_file(omlx_service.settings_file(data_dir), profile_of(_config(ctx)))
    if changed or loosened:
        ctx.step("Starting oMLX with its settings")
    omlx_service.start(ctx, data_dir, homebrew_omlx.program(), changed=changed or loosened)


def _leave_the_homebrew_service(ctx) -> None:
    """Stop famstack's oMLX of an earlier release, a Homebrew service with
    `~/.omlx`, once: the marker `omlx-managed` says famstack started it.
    The formula and its settings stay the admin's."""
    marker = STATE_DIR / "omlx-managed"
    if not marker.exists():
        return
    ctx.step("Moving oMLX from Homebrew's service to famstack's own folder")
    try:
        ctx.shell("brew services stop omlx")
    except RuntimeError:
        pass
    marker.unlink()


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

    With speech off it neither writes nor loads the service. A Whisper
    that already runs keeps running: turning speech off stops nothing.
    """
    # Best-effort: reconciliation needs the real Stack for paths and
    # config. A context without one (the start-guard tests, an exotic
    # embedding) simply skips it — config validation above is this
    # hook's contract, keeping the plist current is a courtesy.
    if getattr(ctx, "stack", None) is None or speech_is_off(_config(ctx)):
        return
    mod = _install_hook()
    data_dir = Path(ctx.stack.data)
    whisper_bin = data_dir / "ai" / "whisper.cpp" / "build" / "bin" / "whisper-server"
    model_path = data_dir / "ai" / "whisper-models" / mod.WHISPER_MODEL
    if not whisper_bin.exists() or not model_path.exists():
        return  # whisper never installed here (or opted out) — nothing to reconcile
    state_dir = Path(__file__).resolve().parent.parent / ".state"
    mod._setup_whisper_launchd(ctx, data_dir, whisper_bin, model_path, state_dir)


def _config(ctx) -> dict:
    """The parsed stack.toml, or nothing from a context without a Stack
    (the start-guard tests, an embedding), which then gets the defaults."""
    stack = getattr(ctx, "stack", None)
    return stack.config if stack is not None else {}


def _install_hook():
    """The install hook, for the parts of setup a start repeats, loaded as
    the framework loads a hook: in the stacklet's package, so its own
    imports from the stacklet resolve."""
    from stack.stacklet_code import load

    hooks = Path(__file__).resolve().parent
    return load(hooks.parent, hooks / "on_install.py")
