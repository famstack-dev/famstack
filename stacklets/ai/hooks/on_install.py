"""AI stacklet first-run install — native macOS services with Metal GPU.

Sets up:
  1. oMLX       — MLX inference with SSD caching (brew) [managed provider only],
     run by famstack in a folder of its own under its own LaunchAgent
     (`omlx_service.py`); the oMLX app's `~/.omlx` stays as it is
  2. whisper.cpp — speech-to-text (built from source), unless an oMLX
     the admin runs transcribes (`speech_on_omlx.py`)

The house voice's speech model goes into oMLX in on_install_success.

Runs once on first `stack up ai`. After that, `on_start` starts both
services on every `stack up ai`, through the lifecycle they share
(`host_services.py`), and `on_stop` stops both.
"""

import plistlib
import shlex
import shutil
import time
from pathlib import Path

from stack.profile import profile_of
from stack.prompt import section, dim, done, out, warn

from .. import homebrew_omlx, host_services, omlx_app, omlx_service
from ..omlx import PLACEHOLDER_KEY, give_new_key, managed_key
from ..omlx_fit import fit_settings_file
from ..speech_on_omlx import speech_on_omlx
from ..speech_setting import speech_is_off


OMLX_PORT = omlx_service.PORT
# Where oMLX is published. Homebrew 6 refuses to load a formula from a
# third-party tap until that tap is trusted, so the trust step in
# `_install_omlx_formula` is what stands between a working `stack up ai`
# and an install that dies at the first brew command.
# Homebrew 7 asks before an install that brings dependencies, and the
# question is buried in `stack up ai`'s output, where a script or an agent
# waits on it for good. The admin already said yes to setting up AI. Older
# Homebrew ignores the variable, where it would refuse `--yes`.
BREW_INSTALL = "HOMEBREW_NO_ASK=1 brew install"

OMLX_TAP = "jundot/omlx"
OMLX_TAP_URL = "https://github.com/jundot/omlx"
WHISPER_MODEL = "ggml-large-v3-turbo.bin"
WHISPER_MODEL_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{WHISPER_MODEL}"
# Floor for "this download finished". The model is ~1.5 GB; anything far
# under that is a download the network cut short. Existence alone is not
# enough: a half-file loads far enough to report "not all tensors loaded"
# and then abort, so the install would keep saying "model ready" while
# whisper died on every boot. Deliberately loose so a slightly smaller
# re-quantisation upstream does not trip it.
WHISPER_MODEL_MIN_BYTES = 1_000_000_000
WHISPER_PORT = 42062
# How long the install waits for its new oMLX to list its models: the
# guard's wait before every start (10 s) and oMLX's own start. A slower
# one is a warning, not a failure.
OMLX_START_SECONDS = 60
PLIST_LABEL = host_services.WHISPER


def run(ctx):
    data_dir = Path(ctx.stack.data)
    stacklet_dir = Path(__file__).resolve().parent.parent
    state_dir = stacklet_dir / ".state"
    state_dir.mkdir(exist_ok=True)

    provider = ctx.cfg("provider", default="")

    # ── oMLX (managed provider only) ────────────────────────────────
    if provider == "managed":
        install_omlx(ctx, state_dir)

    # ── Whisper ─────────────────────────────────────────────────────
    # Speech off (`[ai] speech = false`, as a small Mac is installed, or
    # `--no-voice`) skips the Whisper build and its model download, the
    # slowest part of `stack up ai`.
    if speech_is_off(ctx.stack.config):
        dim("Speech is off: skipping the Whisper install")
    elif speech_on_omlx(ctx):
        # The oMLX the admin runs transcribes: voice messages go to the
        # AI server with `[ai] whisper_model`, and nothing is built.
        pass
    else:
        _install_whisper(ctx, data_dir, state_dir)
        # A dedicated speech server. Without it voice messages go to the
        # AI server, which may not transcribe.
        ctx.cfg("whisper_url", "http://localhost:42062/v1")

    section("Setup complete")


def _install_omlx_formula(ctx) -> None:
    """Tap, trust, and install oMLX, saying out loud what is being trusted.

    Homebrew 6 will not load a formula from a tap outside its own
    repositories until that tap has been trusted, and the refusal is
    fatal: `brew install` exits non-zero, `on_install` fails, and the AI
    stacklet cannot be set up at all. So the trust is not an optimisation,
    it is the install. That makes it exactly the kind of thing to say out
    loud rather than slip past the user inside a compound shell command:
    we are widening what Homebrew will execute on their machine, and they
    should be able to see it happen and undo it.

    Split into three steps for the same reason the one-liner was a
    problem: chained with `&&`, a failure anywhere reported the whole
    string, so the trust refusal looked like a tap failure.

    `brew trust` arrived in Homebrew 6. On older versions there is no gate
    to clear, so a missing subcommand means the job is already done.
    """
    ctx.step("Installing oMLX via Homebrew...")
    ctx.shell_live(f"brew tap {OMLX_TAP} {OMLX_TAP_URL}")

    out(f"Trusting the {OMLX_TAP} Homebrew tap")
    dim("  Homebrew asks before running formulas from outside its own")
    dim("  repositories. This one publishes oMLX, the engine that runs")
    dim("  language models on your Mac's GPU.")
    dim(f"  To undo later: brew untrust {OMLX_TAP}")
    try:
        ctx.shell(f"brew trust {OMLX_TAP}")
    except RuntimeError:
        dim("  (this Homebrew has no trust gate — nothing to do)")

    ctx.shell_live(f"{BREW_INSTALL} omlx --with-grammar")


def install_omlx(ctx, state_dir: Path):
    section("oMLX", "MLX inference (Metal GPU)")
    # Never beside the oMLX app: two MLX servers on one graphics chip can
    # crash the Mac. Stops the run before anything is installed (`omlx_app`).
    omlx_app.refuse_while_running()
    # A settings file it cannot read stops the run before Homebrew runs:
    # it holds the key the family's apps use (`omlx_service.read_settings`).
    data_dir = Path(ctx.stack.data)
    omlx_service.read_settings(omlx_service.settings_file(data_dir))

    if shutil.which("brew") is None:
        warn("Homebrew is required to install oMLX.")
        from stack.prompt import out as _out, nl as _nl
        _out("Install it with:")
        _nl()
        _out('  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
        _nl()
        _out("After installing Homebrew, run:")
        _nl()
        _out("  ./stack up ai")
        _nl()
        raise RuntimeError("Homebrew not found")

    # The program only: an oMLX the admin installed with Homebrew keeps its
    # own settings and service. famstack runs it in a folder of its own.
    if homebrew_omlx.installed():
        done("oMLX already installed with Homebrew: famstack uses the program")
    else:
        _install_omlx_formula(ctx)
    # Homebrew installs the release its tap offers, which can be a release
    # candidate. The run says which one, and the answer check at the end of
    # the run catches a build that cannot answer.
    done(f"oMLX {homebrew_omlx.version() or '(version unknown)'}")

    host = ctx.env.get("PORT_BIND_IP") or "127.0.0.1"
    omlx_service.configure(data_dir, host=host)
    fit_settings_file(omlx_service.settings_file(data_dir), profile_of(ctx.stack.config))
    key = _key_for_omlx(ctx, data_dir, host)

    ctx.step("Starting oMLX...")
    omlx_service.start(ctx, data_dir, homebrew_omlx.program(), changed=True)

    ctx.step("Waiting for oMLX to start...")
    # The key goes in a request header, never on a command line.
    url = ctx.cfg("openai_url", default="") or f"http://localhost:{OMLX_PORT}/v1"
    if not omlx_service.answers(url, key, OMLX_START_SECONDS):
        warn(f"oMLX not responding on port {OMLX_PORT}. It may still be starting.")

    done(f"oMLX running on port {OMLX_PORT}, in {omlx_service.folder(data_dir)}")

    # Save managed endpoint to stack.toml
    ctx.cfg("openai_url", f"http://localhost:{OMLX_PORT}/v1")


def _key_for_omlx(ctx, data_dir: Path, host: str) -> str:
    """The key famstack's oMLX starts with, in its settings in `data_dir`
    and in `[ai] openai_key`. Returns it; never prints it.

    A new install gets a generated key: oMLX listens on every interface
    in port mode, and the placeholder `local` would let every device at
    home use it. A real key that famstack's settings or `[ai] openai_key`
    already hold stays (`omlx.managed_key`), so an install run again after
    it failed halfway, or an instance set up by an earlier release, keeps
    the key its apps know. The oMLX app's key is never read.
    """
    key = managed_key(data_dir, ctx.cfg("openai_key", default=""))
    if key == PLACEHOLDER_KEY:
        return give_new_key(data_dir, lambda k: ctx.cfg("openai_key", k))
    omlx_service.configure(data_dir, host=host, key=key)
    ctx.cfg("openai_key", key)
    return key


def _install_whisper(ctx, data_dir: Path, state_dir: Path):
    section("Whisper", "Speech-to-text (Metal GPU)")

    whisper_dir = data_dir / "ai" / "whisper.cpp"
    model_dir = data_dir / "ai" / "whisper-models"
    log_dir = data_dir / "ai" / "logs"
    model_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Build dependencies
    missing = []
    for dep in ("cmake", "ffmpeg"):
        try:
            ctx.shell(f"brew list {dep}")
        except RuntimeError:
            missing.append(dep)

    if missing:
        ctx.step(f"Installing build dependencies: {' '.join(missing)}...")
        ctx.shell_live(f"{BREW_INSTALL} {' '.join(missing)}")
        done("Dependencies ready")

    # Clone source
    whisper_bin = whisper_dir / "build" / "bin" / "whisper-server"

    if not whisper_dir.exists():
        ctx.step("Cloning whisper.cpp repository...")
        ctx.shell_live(f"git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git {whisper_dir}")
        done("Source code ready")

    # Build
    if not whisper_bin.exists():
        ctx.step("Configuring build (Metal GPU + HTTP server)...")
        ctx.shell(f"cmake -B {whisper_dir}/build -S {whisper_dir} -DWHISPER_BUILD_SERVER=ON -DGGML_METAL=ON")

        ctx.step("Compiling (this takes 1-2 minutes, using all CPU cores)...")
        ctx.shell_live(f"cmake --build {whisper_dir}/build -j --config Release")

        if not whisper_bin.exists():
            raise RuntimeError(f"Build failed — check {whisper_dir}/build/ for errors")
        done("whisper-server built successfully")
    else:
        done("whisper-server already built")

    # Download model
    model_path = model_dir / WHISPER_MODEL
    have = model_path.exists() and model_path.stat().st_size >= WHISPER_MODEL_MIN_BYTES
    if not have:
        if model_path.exists():
            size_mb = model_path.stat().st_size // (1024 * 1024)
            dim(f"  Existing model is only {size_mb} MB — re-downloading.")
            model_path.unlink()
        ctx.step("Downloading Whisper model: large-v3-turbo (~1.5 GB)...")
        dim("  One-time download. OpenAI's distilled model — nearly")
        dim("  identical accuracy to large-v3, but 6x faster on Metal GPU.")
        # -f so an HTTP error page is never written out as a model file.
        ctx.shell_live(
            shlex.join(["curl", "-fL", "--progress-bar", "-o", str(model_path), WHISPER_MODEL_URL])
        )
        if (not model_path.exists()
                or model_path.stat().st_size < WHISPER_MODEL_MIN_BYTES):
            raise RuntimeError(
                "Whisper model download did not complete. Run './stack up ai' "
                "again to resume. Voice transcription stays off until it does."
            )
        done("Model downloaded")
    else:
        done("Whisper model ready (large-v3-turbo)")

    # launchd service
    start_whisper(ctx, data_dir, whisper_bin, model_path)


def start_whisper(ctx, data_dir: Path, whisper_bin: Path, model_path: Path) -> None:
    """Run Whisper under its LaunchAgent (`whisper_agent`), through the
    lifecycle famstack's oMLX shares (`host_services.start`), and wait
    for it to answer when it was (re)started.

    Idempotent by content: when what is on disk already matches and it
    runs, nothing is written and the service is not bounced. on_start
    calls this on every `stack up ai`, which is how a flag change here
    reaches existing installs on a plain restart instead of waiting for a
    `stack setup ai` nobody thinks to run.
    """
    started = host_services.start(ctx.shell, whisper_agent(ctx, data_dir, whisper_bin, model_path))
    if not started:
        return
    section("Whisper server", "launchd service")
    ctx.step("Waiting for whisper-server to load model...")
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            ctx.shell(f"curl -sf --max-time 3 http://127.0.0.1:{WHISPER_PORT}/")
            done(f"whisper-server responding on port {WHISPER_PORT}")
            return
        except RuntimeError:
            time.sleep(2)

    warn("whisper-server not responding yet — model loading can take up to 30s")
    warn(f"Check logs: tail -f {data_dir}/ai/logs/whisper-server.log")


def whisper_agent(ctx, data_dir: Path, whisper_bin: Path, model_path: Path) -> host_services.Agent:
    """Whisper's LaunchAgent and the wrapper it runs, with the household's
    language and the framework's bind address from `ctx`."""
    # Wrapper script: launchd doesn't inherit PATH, ffmpeg needs it.
    # The path is quoted for the shell, so a data dir with `&`, `$` or a
    # quote stays one path.
    wrapper = data_dir / "ai" / "famstack-whisper"
    wrapper_text = (
        "#!/bin/bash\n"
        'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"\n'
        f'exec {shlex.quote(str(whisper_bin))} "$@"\n'
    )

    # Whisper hears what the household speaks: [core].language first,
    # with the ai-stacklet language (primarily a TTS-voice knob) as the
    # fallback. Pinning beats auto-detection, which free-falls on
    # silence and noise — but pinning the wrong language would be worse
    # than auto, so this must never read a voice preference as a
    # transcription language when the household says otherwise.
    language = ctx.stack._cfg("core", "language",
                              ctx.cfg("language", default="auto"))

    # The framework's bind rule, as every container port follows it: all
    # interfaces in port mode so the household's other machines reach
    # it, loopback in domain mode where the reverse proxy is the way in.
    host = ctx.env.get("PORT_BIND_IP") or "127.0.0.1"

    log = str(data_dir / "ai" / "logs" / "whisper-server.log")
    # Built with `plistlib`, so a path with `&` or `<` is written as it is.
    plist_content = plistlib.dumps({
        "Label": PLIST_LABEL,
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(data_dir / "ai"),
        "ProgramArguments": [
            str(wrapper), "--model", str(model_path), "--host", host,
            "--port", str(WHISPER_PORT),
            "--inference-path", "/v1/audio/transcriptions", "--convert",
            # The language comes from config: auto-detection free-falls on
            # silence and noise (measured: baby sounds transcribed as CJK).
            # --max-context 0 stops a hallucinated segment from seeding the
            # next one, and --suppress-nst drops non-speech tokens; each of
            # the three flags fixed loops the other two did not, and only
            # the combination cleared every recording behind the September
            # 2026 runaway generation.
            "--language", language, "--max-context", "0", "--suppress-nst",
            "--threads", "4",
        ],
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }, sort_keys=False).decode()

    return host_services.Agent(PLIST_LABEL, plist_content, files={wrapper: wrapper_text})
