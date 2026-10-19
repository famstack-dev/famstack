"""AI stacklet on_start_ready: download the voices of the house's languages.

Runs on every `stack up ai`, after the speech container answers. The
voice map gives voices to models the speech image never downloads (it
fetches two English ones when it starts), and Piper answers a voice
whose model is missing with a silent MP3. So the models of the house's
languages are fetched here, with the image's own download script:
`[ai] language`, the language the stack speaks, and `[core] language`,
the family's. Models in other languages are left out, since the German
one alone is 114 MB.

A model already on disk is not fetched again. One that cannot be
downloaded is a warning that names it, never a failed `up`: chat,
transcription and the other voices work without it.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from speech import VOICE_MAP, voice_models

# The speech image's download script, relative to its working directory.
DOWNLOAD_SCRIPT = "download_voices_tts-1.sh"


def run(ctx):
    # `--no-voice` leaves the speech container out (see on_start), so
    # there is nothing to download into.
    if os.environ.get("STACK_AI_NO_VOICE") == "1":
        return

    languages = [ctx.cfg("language", default="en"),
                 ctx.stack._cfg("core", "language", "")]
    # The container's /app/voices, bind-mounted from here (docker-compose.yml).
    voices = Path(ctx.env["SPEECH_DATA_DIR"]) / "voices"

    # Checked on this side rather than left to the script, which also
    # skips a model it has: it fetches Piper's voice list from Hugging
    # Face on every call, and exits 1 when the last model is already there.
    for model in voice_models(VOICE_MAP.read_text(), languages):
        if not _on_disk(voices, model):
            _download(ctx, voices, model)


def _download(ctx, voices: Path, model: str) -> None:
    """Fetch one model, or warn and name it.

    One call per model, so a failure is about that model. The file on
    disk decides whether it worked, since the script's exit code is
    only that of its last command.
    """
    ctx.step(f"Downloading voice {model}")
    reason = ""
    try:
        ctx.run_in_container(["bash", DOWNLOAD_SCRIPT, model], service="speech")
    except RuntimeError as e:
        reason = f" ({e})"
    if not _on_disk(voices, model):
        ctx.warn(f"Voice model {model} could not be downloaded{reason}. "
                 "The voices that use it stay silent until './stack up ai' "
                 "downloads it.")


def _on_disk(voices: Path, model: str) -> bool:
    return (voices / f"{model}.onnx").exists()
