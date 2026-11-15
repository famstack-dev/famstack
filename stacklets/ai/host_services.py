"""famstack's LaunchAgents: one lifecycle for the AI's host services.

    from .. import host_services

    agent = host_services.Agent(host_services.WHISPER, plist, files={wrapper: text})
    host_services.start(ctx.shell, agent)            # "loaded", "restarted" or ""
    host_services.stop(ctx.shell, host_services.WHISPER)   # unloaded, its file removed
    host_services.up(ctx.shell)                      # {"dev.famstack.omlx"}: what launchd has

The ai stacklet runs two services on the Mac itself, outside Docker,
because they need the Mac's graphics chip: famstack's oMLX
(`omlx_service.py`) and the Whisper speech server (`whisper_service.py`).
launchd runs each from a LaunchAgent in `~/Library/LaunchAgents`, and
this module is the one place that writes, loads, restarts and removes
them. Each service only says what its agent contains.

One rule holds for both: **an agent's file exists exactly while famstack
wants the service up**, and launchd is asked what runs. launchd loads every file in `LaunchAgents` at
login, so a file left behind after `stack down ai` starts the service
again at the next login. So `stop` removes the file of each agent,
Whisper's and oMLX's alike, and neither starts at the next login:

    stack up ai      start   writes the file, loads it   ─▶ runs, also after a login
    stack down ai    stop    stops the job, removes the file ─▶ stays down, also after a login
    a failed run     stop    for each agent the run added (`cli/switch.py`)

launchd is asked through the framework's `stack.launchd`, which trusts
its answers and not its exit codes: a stop returns only once launchd let
the job go, and a load fails when launchd does not have the job
afterwards. A restart (`stack restart ai`) is a stop and a start within
the same second, and depends on both.

`start` is idempotent by content: a file that already says the same is
not written again, and a loaded service is not restarted unless the
caller says its settings changed. A restart keeps the job in launchd
(`launchctl kickstart -k`); only a changed agent file is stopped and
loaded, with the old file put back when the new one does not load. A loaded agent whose process does not
run (famstack's oMLX steps aside while the oMLX app runs, and exits) is
restarted, so `stack up ai` brings it back.

Commands go through a runner, `run(command) -> stdout`, which raises
`RuntimeError` when the command fails: a hook passes `ctx.shell`, a
command without a hook context passes `shell`. Every path is quoted for
the shell, so a home such as `/Users/o'brien` stays one path.

What it does not do: decide what an agent runs (the services do), wait
for a service to answer (`whisper_service.start` waits for Whisper,
`omlx_service.answers` for oMLX), or touch an agent famstack did not write,
such as Homebrew's `homebrew.mxcl.omlx` (`homebrew_service.py`).
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from stack import launchd

__all__ = ["OMLX", "WHISPER", "LABELS", "Agent", "agent_file", "start", "stop",
           "restart_commands", "up", "shell"]

# The labels of famstack's agents for the AI, also their file names.
OMLX = "dev.famstack.omlx"
WHISPER = "dev.famstack.whisper"
LABELS = (OMLX, WHISPER)

# How long a launchctl command may take before a hook gives up on it.
LAUNCHCTL_SECONDS = 60

Runner = Callable[[str], str]


@dataclass(frozen=True)
class Agent:
    """One LaunchAgent as famstack writes it: its `label`, the `plist`
    text, and the `files` it runs (a wrapper script, path to text), which
    are written beside it and made executable.

        Agent(WHISPER, plist_text, files={Path("~/famstack-data/ai/famstack-whisper"): script})
    """

    label: str
    plist: str
    files: dict[Path, str] = field(default_factory=dict)


def agent_file(label: str) -> Path:
    """The file of the agent `label` in the home's `LaunchAgents`. Read from
    the home directory on each call, so a test's home is the one used.

        agent_file(OMLX)    # Path("/Users/homer/Library/LaunchAgents/dev.famstack.omlx.plist")
    """
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def start(run: Runner, agent: Agent, *, changed: bool = False) -> str:
    """Run the service of `agent`: write its files and its agent file when
    they differ from what is on disk, load the agent when launchd does not
    have it, and restart it when it is loaded and something it reads
    changed (`changed`, its files, its agent file) or its process does not
    run.

    A restart keeps the job in launchd (`_restart`), so a failure cannot
    leave the Mac without the service that ran. Only a changed agent file
    needs launchd to unload and load it; when that load fails or is
    interrupted, the agent file that ran before is written back and loaded
    again, and the error is raised.

    Returns "loaded", "restarted", or "" when a running service was left
    as it is. Raises `RuntimeError` when launchd cannot load it
    (`launchd.load`).

        start(ctx.shell, agent)                 # "loaded" after `stack down ai`
        start(ctx.shell, agent)                 # "" right after
        start(ctx.shell, agent, changed=True)   # "restarted": it reads its settings again
    """
    path = agent_file(agent.label)
    before = path.read_text() if path.exists() else None
    files_changed = _write_files(agent)
    if not launchd.loaded(run, agent.label):
        _write_agent_file(agent)
        launchd.load(run, path)
        return "loaded"
    if before != agent.plist:
        _reload(run, agent, before)
        return "restarted"
    if files_changed or changed or not launchd.running(run, agent.label):
        _restart(run, agent.label)
        return "restarted"
    return ""


def stop(run: Runner, label: str) -> bool:
    """Stop the service `label` and remove its agent file, so launchd does
    not start it again at the next login. Returns whether there was one to
    stop: a job launchd has, or a file login would load.

    launchd is asked, not the file: a job is stopped by its label
    (`launchd.stop`), also when its file is gone. The file goes only
    after launchd let go of the job, and `stop` returns only then: a start
    right after it would otherwise load into a job that is still leaving.
    A stop launchd refuses, or a job launchd still lists after
    `launchd.STOP_SECONDS`, raises `RuntimeError` and keeps the file,
    because a job that runs on without its file is one famstack can no
    longer find.

        stop(ctx.shell, WHISPER)    # True after `stack up ai`, False after
    """
    path = agent_file(label)
    loaded = launchd.stop(run, label)
    if not path.exists():
        return loaded
    path.unlink()
    return True



def restart_commands(label: str) -> list[list[str]]:
    """The commands that restart the service `label`, as argument lists,
    for a caller that runs them itself (`stack ai key new`).

        restart_commands(OMLX)    # [["launchctl", "kickstart", "-k", "gui/501/dev.famstack.omlx"]]
    """
    return [["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"]]


def up(run: Runner) -> set[str]:
    """The labels of famstack's agents that are up: the jobs launchd has,
    and the agent files it would load at the next login.

        up(shell)    # {"dev.famstack.omlx", "dev.famstack.whisper"}
    """
    return {label for label in LABELS
            if launchd.loaded(run, label) or agent_file(label).exists()}


def shell(command: str, timeout: float = LAUNCHCTL_SECONDS) -> str:
    """The runner for a caller without a hook context: runs `command` in a
    shell and returns its output, or raises `RuntimeError` with its error,
    also when it runs longer than `timeout` seconds.

        stop(shell, OMLX)
    """
    try:
        done = subprocess.run(command, shell=True, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{command}: no answer within {timeout} s") from None
    if done.returncode != 0:
        raise RuntimeError(f"{command}: {done.stderr.strip() or f'exit {done.returncode}'}")
    return done.stdout


# ── Writing ──────────────────────────────────────────────────────────────

def _write_files(agent: Agent) -> bool:
    """Write the files the agent runs where they differ from what is on
    disk, executable. Returns whether any was written."""
    written = False
    for path, text in agent.files.items():
        if not path.exists() or path.read_text() != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            written = True
        path.chmod(0o755)
    return written


def _write_agent_file(agent: Agent) -> None:
    path = agent_file(agent.label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(agent.plist)


def _restart(run: Runner, label: str) -> None:
    """Restart the service `label` in place, so it reads its settings
    again: `launchctl kickstart -k` stops its process and starts it, and
    the job never leaves launchd. Also starts a loaded job whose process
    had ended (famstack's oMLX after it stepped aside for the oMLX app)."""
    for command in restart_commands(label):
        run(shlex.join(command))


def _reload(run: Runner, agent: Agent, before: str | None) -> None:
    """Replace a loaded agent whose file changed: stop it (`launchd.stop`
    returns once launchd let it go), write the new file, load it. When the
    load fails, or anything ends the run on the way, the file `before` is
    written back and loaded again, so the service that ran keeps running,
    and the error is raised."""
    path = agent_file(agent.label)
    launchd.stop(run, agent.label)
    try:
        _write_agent_file(agent)
        launchd.load(run, path)
    except BaseException:
        if before is not None:
            path.write_text(before)
            try:
                launchd.load(run, path)
            except RuntimeError:
                pass
        raise
