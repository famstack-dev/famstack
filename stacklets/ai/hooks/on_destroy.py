"""AI stacklet destroy hook: remove the host services famstack made.

Runs on `stack destroy ai`, after `on_stop`. famstack's oMLX and Whisper
lose their LaunchAgents (`host_services.stop`, again here, so a destroy
after a failed stop still removes them), and Whisper loses its wrapper.
An earlier release's Homebrew service stops (`homebrew_service`), and
LM Studio is removed when its marker says famstack installed it. The
stacklet's markers go last.

famstack's oMLX folder and Whisper's build are in the data dir, which
the destroy removes after this hook. The Homebrew formula, the models in
`~/.omlx/models` and the oMLX app stay: famstack removes only what it
made. A failure here is reported and never stops the destroy.
"""

import shutil
from pathlib import Path

from .. import homebrew_service, host_services
from .on_stop import LM_STUDIO_MARKER, STATE_DIR, stop_lm_studio

__all__ = ["run"]

# Whisper's wrapper, which its LaunchAgent runs (`hooks/on_install.py`).
WHISPER_WRAPPER = Path("ai") / "famstack-whisper"


def run(ctx):
    """Remove famstack's agents, Whisper's wrapper, an earlier release's
    services and the stacklet's markers."""
    for label in host_services.LABELS:
        if host_services.stop(ctx.shell, label):
            ctx.step(f"{label} launchd service removed")
    (Path(ctx.stack.data) / WHISPER_WRAPPER).unlink(missing_ok=True)
    if homebrew_service.was_famstacks(STATE_DIR):
        homebrew_service.stop(ctx.shell)
    lm_studio = Path.home() / ".lmstudio"
    if (lm_studio / "bin" / "lms").exists():
        stop_lm_studio(ctx)
    # Only an LM Studio famstack installed is removed.
    if (STATE_DIR / LM_STUDIO_MARKER).exists() and lm_studio.is_dir():
        shutil.rmtree(lm_studio)
        ctx.step("LM Studio uninstalled")
    shutil.rmtree(STATE_DIR, ignore_errors=True)
