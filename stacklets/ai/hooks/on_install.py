"""AI stacklet first-run install — native macOS services with Metal GPU.

Sets up:
  1. oMLX       — MLX inference with SSD caching (brew) [managed provider only]
  2. whisper.cpp — speech-to-text (built from source) [always]

TTS (Piper) is handled by docker-compose, not this script.

Runs once on first `stack up ai`. After that, the CLI just checks
service health and restarts if needed.
"""

import json
import os
import shutil
import time
from pathlib import Path

from stack.prompt import section, dim, done, warn


OMLX_PORT = 42060
WHISPER_MODEL = "ggml-large-v3-turbo.bin"
WHISPER_MODEL_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{WHISPER_MODEL}"
WHISPER_PORT = 42062
PLIST_LABEL = "dev.famstack.whisper"


def run(ctx):
    data_dir = Path(ctx.stack.data)
    stacklet_dir = Path(__file__).resolve().parent.parent
    state_dir = stacklet_dir / ".state"
    state_dir.mkdir(exist_ok=True)

    provider = ctx.cfg("provider", default="")

    # ── oMLX (managed provider only) ────────────────────────────────
    if provider == "managed":
        _install_omlx(ctx, state_dir)

    # ── Whisper (always) ────────────────────────────────────────────
    _install_whisper(ctx, data_dir, state_dir)

    section("Setup complete")


def _install_omlx(ctx, state_dir: Path):
    section("oMLX", "MLX inference (Metal GPU)")

    if shutil.which("brew") is None:
        warn("Homebrew is required to install oMLX.")
        from stack.prompt import out as _out, nl as _nl
        _out("Install it with:")
        _nl()
        _out('  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
        _nl()
        _out("After installing Homebrew, run:")
        _nl()
        _out("  stack up ai")
        _nl()
        raise RuntimeError("Homebrew not found")

    # Install if needed
    try:
        ctx.shell("brew list omlx")
        done("oMLX already installed")
    except RuntimeError:
        ctx.step("Installing oMLX via Homebrew...")
        ctx.shell_live("brew tap jundot/omlx https://github.com/jundot/omlx && brew install omlx --with-grammar")
        done("oMLX installed")

    # Configure: port 42060, shared models directory
    omlx_settings = Path.home() / ".omlx" / "settings.json"
    omlx_settings.parent.mkdir(parents=True, exist_ok=True)

    settings = {}
    if omlx_settings.exists():
        try:
            settings = json.loads(omlx_settings.read_text())
        except Exception:
            pass
    settings.setdefault("server", {})["port"] = OMLX_PORT
    settings.setdefault("model", {})["model_dir"] = str(Path.home() / ".omlx" / "models")
    settings.setdefault("auth", {})["api_key"] = ctx.cfg("openai_key", default="local")
    omlx_settings.write_text(json.dumps(settings, indent=2))

    # Start service and wait for it
    ctx.step("Starting oMLX service...")
    ctx.shell("brew services restart omlx")
    (state_dir / "omlx-managed").touch()

    ctx.step("Waiting for oMLX to start...")
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            ctx.shell(f'curl -sf --max-time 2 http://localhost:{OMLX_PORT}/v1/models')
            break
        except RuntimeError:
            time.sleep(2)
    else:
        warn(f"oMLX not responding on port {OMLX_PORT} — it may still be starting")

    done(f"oMLX running on port {OMLX_PORT}")

    # Save managed endpoint to stack.toml
    ctx.cfg("openai_url", f"http://localhost:{OMLX_PORT}/v1")
    ctx.cfg("openai_key", "local")


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
        ctx.shell_live(f"brew install {' '.join(missing)}")
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
    if not model_path.exists():
        ctx.step("Downloading Whisper model: large-v3-turbo (~1.5 GB)...")
        dim("  One-time download. OpenAI's distilled model — nearly")
        dim("  identical accuracy to large-v3, but 6x faster on Metal GPU.")
        ctx.shell_live(f'curl -L --progress-bar -o "{model_path}" "{WHISPER_MODEL_URL}"')
        done("Model downloaded")
    else:
        done("Whisper model ready (large-v3-turbo)")

    # launchd service
    _setup_whisper_launchd(ctx, data_dir, whisper_bin, model_path, state_dir)


def _setup_whisper_launchd(ctx, data_dir: Path, whisper_bin: Path, model_path: Path, state_dir: Path):
    section("Whisper server", "launchd service")

    agents_dir = Path.home() / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    # Wrapper script — launchd doesn't inherit PATH, ffmpeg needs it
    wrapper = data_dir / "ai" / "famstack-whisper"
    wrapper.write_text(
        f"#!/bin/bash\n"
        f'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"\n'
        f'exec "{whisper_bin}" "$@"\n'
    )
    wrapper.chmod(0o755)

    plist_path = agents_dir / f"{PLIST_LABEL}.plist"
    plist_path.write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        f'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        f'<plist version="1.0">\n'
        f'<dict>\n'
        f'  <key>Label</key>\n'
        f'  <string>{PLIST_LABEL}</string>\n'
        f'  <key>RunAtLoad</key>\n'
        f'  <true/>\n'
        f'  <key>KeepAlive</key>\n'
        f'  <true/>\n'
        f'  <key>WorkingDirectory</key>\n'
        f'  <string>{data_dir}/ai</string>\n'
        f'  <key>ProgramArguments</key>\n'
        f'  <array>\n'
        f'    <string>{wrapper}</string>\n'
        f'    <string>--model</string>\n'
        f'    <string>{model_path}</string>\n'
        f'    <string>--host</string>\n'
        f'    <string>127.0.0.1</string>\n'
        f'    <string>--port</string>\n'
        f'    <string>{WHISPER_PORT}</string>\n'
        f'    <string>--inference-path</string>\n'
        f'    <string>/v1/audio/transcriptions</string>\n'
        f'    <string>--convert</string>\n'
        f'    <string>--language</string>\n'
        f'    <string>auto</string>\n'
        f'    <string>--threads</string>\n'
        f'    <string>4</string>\n'
        f'  </array>\n'
        f'  <key>StandardOutPath</key>\n'
        f'  <string>{data_dir}/ai/logs/whisper-server.log</string>\n'
        f'  <key>StandardErrorPath</key>\n'
        f'  <string>{data_dir}/ai/logs/whisper-server.log</string>\n'
        f'</dict>\n'
        f'</plist>\n'
    )

    ctx.step("Loading whisper-server into launchd...")
    try:
        ctx.shell(f'launchctl unload "{plist_path}"')
    except RuntimeError:
        pass
    ctx.shell(f'launchctl load "{plist_path}"')

    (state_dir / "whisper-managed").touch()

    # Wait for model to load
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
