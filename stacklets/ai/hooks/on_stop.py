"""AI stacklet stop hook: stop the host services famstack runs, so they stay down.

Runs on every `stack down ai` (and before `stack destroy ai`). famstack's
oMLX and Whisper run under LaunchAgents, and both stop the same way
(`host_services.stop`): launchd unloads the agent and its file goes, so
launchd does not start the service again when the admin next logs in.
Each stop returns once launchd no longer lists the job, so the start of
a `stack restart ai` right after it finds the label free.
`stack up ai` writes and loads both again.

An instance an earlier release set up may still run famstack's oMLX as
Homebrew's service (`homebrew_service`): that stops too. LM Studio, which
releases before oMLX installed, stops when its marker says famstack
started it.

A service that does not stop does not keep the others running: each is
stopped in turn, and the hook then fails and names every one that still
runs, so `stack down ai` fails and the AI still counts as up.

What it does not do: stop an AI server famstack does not run, such as the
oMLX app, or Homebrew's service when an earlier release did not start it.
"""

import shlex
from pathlib import Path

from .. import homebrew_service, host_services

__all__ = ["run"]

# The marker releases before 0.5 wrote when they started LM Studio, in
# the stacklet's one folder of markers (`homebrew_service.STATE_DIR`).
LM_STUDIO_MARKER = "lmstudio-managed"
NAMES = {host_services.OMLX: "oMLX", host_services.WHISPER: "Whisper"}


def run(ctx):
    """Stop famstack's oMLX and Whisper, and what an earlier release
    started; each one that was up is named. Raises `RuntimeError` naming
    each service that did not stop, after trying them all."""
    still_running: list[str] = []
    for label in host_services.LABELS:
        try:
            if host_services.stop(ctx.shell, label):
                ctx.step(f"{NAMES[label]} stopped")
        except RuntimeError as e:
            still_running.append(f"{NAMES[label]}: {e}")
    if homebrew_service.was_famstacks():
        try:
            homebrew_service.stop(ctx.shell)
            ctx.step("oMLX stopped (Homebrew's service)")
        except RuntimeError as e:
            still_running.append(f"oMLX (Homebrew's service): {e}")
    if (homebrew_service.STATE_DIR / LM_STUDIO_MARKER).exists():
        stop_lm_studio(ctx)
    if still_running:
        raise RuntimeError("did not stop: " + "; ".join(still_running))


def stop_lm_studio(ctx) -> None:
    """Stop LM Studio's server and daemon, which releases before oMLX
    installed; a failure is ignored, as it may not run."""
    lms = Path.home() / ".lmstudio" / "bin" / "lms"
    if lms.exists():
        for command in (shlex.join([str(lms), "server", "stop"]),
                        shlex.join([str(lms), "daemon", "down"])):
            try:
                ctx.shell(command)
            except RuntimeError:
                pass
    ctx.step("LM Studio stopped")
