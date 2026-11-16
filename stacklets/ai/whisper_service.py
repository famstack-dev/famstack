"""whisper_service.py: Whisper, the speech server famstack runs on this Mac.

    from .. import whisper_service

    whisper_service.server(data_dir)       # .../ai/whisper.cpp/build/bin/whisper-server
    whisper_service.model_file(data_dir)   # .../ai/whisper-models/ggml-large-v3-turbo.bin
    whisper_service.start(ctx, data_dir, server, model)   # under its LaunchAgent

whisper.cpp's server transcribes voice messages on the Mac's graphics
chip, at `http://localhost:42062/v1`. The preparation builds it and
downloads its model (`prepare.py`); every `stack up ai` starts it under
its LaunchAgent through the lifecycle famstack's oMLX shares
(`host_services.py`), and `stack down ai` stops both.

The agent is written again on every start and compared by content, so a
flag changed here reaches existing installs on a plain `stack restart ai`.
"""

import plistlib
import shlex
import time
from pathlib import Path

from stack.prompt import done, section, warn

from . import host_services

__all__ = ["LABEL", "PORT", "MODEL", "server", "model_file", "start", "agent"]

LABEL = host_services.WHISPER
PORT = 42062
# The model file, OpenAI's distilled large-v3: nearly the accuracy of
# large-v3, six times faster on the Mac's graphics chip.
MODEL = "ggml-large-v3-turbo.bin"


def server(data_dir: str | Path) -> Path:
    """The built Whisper server under `data_dir`.

        server("/Users/homer/famstack-data")   # .../ai/whisper.cpp/build/bin/whisper-server
    """
    return Path(data_dir) / "ai" / "whisper.cpp" / "build" / "bin" / "whisper-server"


def model_file(data_dir: str | Path) -> Path:
    """Whisper's model under `data_dir`.

        model_file("/Users/homer/famstack-data")   # .../ai/whisper-models/ggml-large-v3-turbo.bin
    """
    return Path(data_dir) / "ai" / "whisper-models" / MODEL


def start(ctx, data_dir: Path, whisper_bin: Path, model_path: Path) -> None:
    """Run Whisper under its LaunchAgent (`agent`), through the
    lifecycle famstack's oMLX shares (`host_services.start`), and wait
    for it to answer when it was (re)started.

    Idempotent by content: when what is on disk already matches and it
    runs, nothing is written and the service is not bounced. on_start
    calls this on every `stack up ai`, which is how a flag change here
    reaches existing installs on a plain restart instead of waiting for a
    `stack setup ai` nobody thinks to run.

        start(ctx, data_dir, server(data_dir), model_file(data_dir))
    """
    (data_dir / "ai" / "logs").mkdir(parents=True, exist_ok=True)   # launchd writes no folder
    started = host_services.start(ctx.shell, agent(ctx, data_dir, whisper_bin, model_path))
    if not started:
        return
    section("Whisper server", "launchd service")
    ctx.step("Waiting for whisper-server to load model...")
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            ctx.shell(f"curl -sf --max-time 3 http://127.0.0.1:{PORT}/")
            done(f"whisper-server responding on port {PORT}")
            return
        except RuntimeError:
            time.sleep(2)

    warn("whisper-server not responding yet — model loading can take up to 30s")
    warn(f"Check logs: tail -f {data_dir}/ai/logs/whisper-server.log")


def agent(ctx, data_dir: Path, whisper_bin: Path, model_path: Path) -> host_services.Agent:
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
        "Label": LABEL,
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(data_dir / "ai"),
        "ProgramArguments": [
            str(wrapper), "--model", str(model_path), "--host", host,
            "--port", str(PORT),
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

    return host_services.Agent(LABEL, plist_content, files={wrapper: wrapper_text})
