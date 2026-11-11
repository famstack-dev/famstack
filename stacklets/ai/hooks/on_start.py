"""AI stacklet start hook — validate config, start native services.

Runs on every `stack up ai`. Validates that the AI provider is
configured before proceeding. This is the on_start contract:
check required config first, raise with a clear fix if missing.
For the managed oMLX it also keeps oMLX's settings in line with the
stack (its address, and its memory and idle unload on a small Mac,
`omlx_fit`) and restarts it when they changed. An oMLX the admin runs
on this Mac, such as the oMLX app, gets the same memory settings through
its admin API, without a restart. Speech off (`[ai] speech = false` or
`--no-voice`) leaves the Whisper service alone.
"""

import shutil
from pathlib import Path

from stack.prompt import out, nl, warn, dim, TEAL, RESET
from stack.profile import profile_of

from ..model_server import model_server
from ..omlx_fit import fit_running_omlx, fit_settings_file
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
        _fit_the_omlx_app(ctx, url)

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
    loosened = _fit_omlx_to_a_small_mac(_config(ctx))

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


def _fit_omlx_to_a_small_mac(config: dict) -> bool:
    """Give the oMLX the stack installed the memory profile's settings
    (`omlx_fit`), in its settings file, which it reads at start only.
    `config` is the parsed `stack.toml`. Returns whether the file changed.
    on_install writes the file before the first start; without it there
    is nothing to reconcile yet."""
    return fit_settings_file(Path.home() / ".omlx" / "settings.json", profile_of(config))


def _fit_the_omlx_app(ctx, url: str):
    """Give an oMLX the admin runs on this Mac, such as the oMLX app, the
    memory profile's settings through its admin API, and say what changed.

    It shares this Mac's memory with the stack, so a small Mac's oMLX
    needs the same ceiling, prefill and idle unload whoever installed it.
    oMLX applies and saves them itself; the app is never restarted. A
    server on another machine, one that is not an oMLX, or a Mac with a
    larger profile is left as it is.
    """
    profile = profile_of(_config(ctx))
    if profile.omlx_ceiling_gb is None:
        return
    server = model_server("external", url, ctx.cfg("openai_key", default=""))
    if server is None or server.asks_first:
        return
    client = server.client()
    if not client.login():
        dim("The oMLX app did not take the API key, so its memory settings stay as they are.")
        return
    for line in fit_running_omlx(client, profile).lines():
        out(line)


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
