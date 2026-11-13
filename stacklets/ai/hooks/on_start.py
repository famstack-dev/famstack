"""AI stacklet start hook — validate config, start native services.

Runs on every `stack up ai`. Validates that the AI provider is
configured before proceeding. This is the on_start contract:
check required config first, raise with a clear fix if missing.
For the managed oMLX it runs famstack's own oMLX in its own folder
(`omlx_service`), keeps its settings in line with the stack (its port,
address and key, and its memory and idle unload on a small Mac,
`omlx_fit`) and restarts it when they changed. Whisper's agent is
written and loaded again too. Both go through one lifecycle
(`host_services`), which `on_stop` reverses. An AI server the stack
does not manage, such as the oMLX app, is never changed. Speech off (`[ai] speech = false` or
`--no-voice`) leaves the Whisper service alone.
"""

from pathlib import Path

from stack.prompt import out, nl, warn, dim, TEAL, RESET
from stack.profile import profile_of

from .. import homebrew_omlx, homebrew_service, host_services, omlx_app, omlx_service
from ..omlx import PLACEHOLDER_KEY
from ..omlx_fit import fit_settings_file
from ..speech_setting import speech_is_off

# How long the move off an earlier release's Homebrew service waits for
# famstack's oMLX to answer before it starts the old one again: the
# guard's wait before every start (10 s, `omlx_service.GuardTiming`), oMLX's
# own start, and a first model load from a cold disk (up to 60 s for the
# default chat model), with 20 s to spare.
MOVE_READY_SECONDS = 90

# The marker of an earlier release's Homebrew service (`homebrew_service`).
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

    if provider == "managed":
        _may_start(ctx)

    if provider == "managed" and not homebrew_omlx.installed():
        # The stack manages the engine, so a missing one is installed:
        # after `stack ai switch managed` on a Mac that has only the voice
        # services, or when a brew cleanup or a migrated machine took it
        # away after setup. The same check as the install's, for the
        # program the LaunchAgent runs (`homebrew_omlx`).
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
    Homebrew service, with `~/.omlx`. Its first start here moves over once
    (`homebrew_service`): it carries the model settings and the key `[ai]`
    sends into famstack's folder, so apps that use the key keep working,
    and stops that service. Only once famstack's oMLX answers is the move
    done. When it does not start or answer, or the run is interrupted, the
    old service starts again and the move waits for the next start.

    A failed start takes back only what it started: famstack's oMLX that
    ran before the run keeps running.
    """
    data_dir = Path(ctx.stack.data)
    ran_before = omlx_service.LABEL in host_services.up(ctx.shell)
    moving = homebrew_service.was_famstacks(STATE_DIR)
    try:
        if moving:
            ctx.step("Moving oMLX from Homebrew's service to famstack's own folder")
            homebrew_service.carry_model_settings(data_dir)
            homebrew_service.stop(ctx.shell)
        _run_famstacks_omlx(ctx, data_dir)
        if moving:
            _wait_until_it_answers(ctx)
    except BaseException:
        # Also on Ctrl-C, from the moment the old service's stop began.
        # The run takes back only what it started: an agent it loaded goes,
        # so launchd does not start it at login, and the old AI runs again.
        # A famstack oMLX that ran before this run keeps running.
        if not ran_before:
            _take_back(ctx)
        if moving:
            # The old service takes port 42060 only once famstack's oMLX let go.
            if not host_services.wait_until_stopped(ctx.shell, omlx_service.LABEL):
                warn("famstack's oMLX still runs, so the old oMLX may not get port 42060.")
            homebrew_service.start_again(ctx.shell)
        raise
    if moving:
        homebrew_service.moved(STATE_DIR)
        if note := homebrew_service.apps_port_note():
            warn(note)


def _wait_until_it_answers(ctx) -> None:
    """Return once famstack's oMLX lists its models at `[ai] openai_url`
    with the key in its settings; raise when it does not within
    `MOVE_READY_SECONDS`. The move keeps the old service as its fallback
    until then."""
    url = ctx.cfg("openai_url", default=f"http://localhost:{omlx_service.PORT}/v1")
    # The key famstack's oMLX took, from its settings: `[ai] openai_key`
    # may be missing, and oMLX refuses an empty one.
    settings = omlx_service.read_settings(omlx_service.settings_file(ctx.stack.data))
    key = settings.get("auth", {}).get("api_key") or PLACEHOLDER_KEY
    if not omlx_service.answers(url, key, MOVE_READY_SECONDS):
        raise RuntimeError(f"famstack's oMLX did not answer at {url} within "
                           f"{MOVE_READY_SECONDS} s, so the stack keeps the oMLX it ran "
                           f"before. Its log: {omlx_service.folder(ctx.stack.data) / 'omlx.log'}")


def _take_back(ctx) -> None:
    """Stop famstack's oMLX that this failed run started. A stop launchd
    refuses is said, not raised, so the run's own error stays the one
    reported."""
    try:
        host_services.stop(ctx.shell, omlx_service.LABEL)
    except RuntimeError as e:
        warn(f"famstack's oMLX could not be stopped after the failed start: {e}")


def _may_start(ctx) -> None:
    """Stop the run before anything changes, also before an install, when
    famstack's oMLX must not start: the oMLX app runs (two MLX servers on
    one graphics chip can crash the Mac, `omlx_app`), or famstack's
    settings file cannot be read (`omlx_service.UnreadableSettings` names
    it). Returns when both checks pass."""
    omlx_app.refuse_while_running()
    omlx_service.read_settings(omlx_service.settings_file(Path(ctx.stack.data)))


def _run_famstacks_omlx(ctx, data_dir: Path) -> None:
    """Write famstack's settings and start its oMLX, restarting it when a
    setting changed. Raises when launchd cannot load it."""
    carried = None
    if not omlx_service.settings_file(data_dir).exists():
        carried = ctx.cfg("openai_key", default="") or PLACEHOLDER_KEY
    host = ctx.env.get("PORT_BIND_IP") or "127.0.0.1"
    changed = omlx_service.configure(data_dir, host=host, key=carried)
    loosened = fit_settings_file(omlx_service.settings_file(data_dir), profile_of(_config(ctx)))
    if changed or loosened:
        ctx.step("Starting oMLX with its settings")
    omlx_service.start(ctx, data_dir, homebrew_omlx.program(), changed=changed or loosened)


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
    mod = _install_hook()
    data_dir = Path(ctx.stack.data)
    whisper_bin = data_dir / "ai" / "whisper.cpp" / "build" / "bin" / "whisper-server"
    model_path = data_dir / "ai" / "whisper-models" / mod.WHISPER_MODEL
    if not whisper_bin.exists() or not model_path.exists():
        return  # whisper never installed here (or opted out): nothing to start
    mod.start_whisper(ctx, data_dir, whisper_bin, model_path)


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
