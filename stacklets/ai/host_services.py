"""famstack's LaunchAgents: one lifecycle for the AI's host services.

    from .. import host_services

    agent = host_services.Agent(host_services.WHISPER, plist, files={wrapper: text})
    host_services.start(ctx.shell, agent)            # "loaded", "restarted" or ""
    host_services.stop(ctx.shell, host_services.WHISPER)   # unloaded, its file removed
    host_services.up(ctx.shell)                      # {"dev.famstack.omlx"}: what launchd has

The ai stacklet runs two services on the Mac itself, outside Docker,
because they need the Mac's graphics chip: famstack's oMLX
(`omlx_service.py`) and the Whisper speech server (`hooks/on_install.py`).
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

`start` is idempotent by content: a file that already says the same is
not written again, and a loaded service is not restarted unless the
caller says its settings changed. A restart keeps the job in launchd
(`launchctl kickstart -k`); only a changed agent file is unloaded and
loaded, with the old file put back when the new one does not load. A loaded agent whose process does not
run (famstack's oMLX steps aside while the oMLX app runs, and exits) is
restarted, so `stack up ai` brings it back.

Commands go through a runner, `run(command) -> stdout`, which raises
`RuntimeError` when the command fails: a hook passes `ctx.shell`, a
command without a hook context passes `shell`. Every path is quoted for
the shell, so a home such as `/Users/o'brien` stays one path.

What it does not do: decide what an agent runs (the services do), wait
for a service to answer (`hooks/on_install.py` waits for Whisper,
`omlx_service.answers` for oMLX), or touch an agent famstack did not write,
such as Homebrew's `homebrew.mxcl.omlx` (`homebrew_service.py`).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["OMLX", "WHISPER", "LABELS", "Agent", "agent_file", "start", "stop",
           "wait_until_stopped", "restart",
           "restart_commands", "up", "shell"]

# The labels of famstack's agents for the AI, also their file names.
OMLX = "dev.famstack.omlx"
WHISPER = "dev.famstack.whisper"
LABELS = (OMLX, WHISPER)

# How long a launchctl command may take before a hook gives up on it.
LAUNCHCTL_SECONDS = 60
# How long a stopped service may take to exit: oMLX unloads its model and
# ends within seconds of TERM; launchd kills it after 20 s.
STOP_SECONDS = 30
STOP_POLL_SECONDS = 0.5

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

    A restart keeps the job in launchd (`restart`), so a failure cannot
    leave the Mac without the service that ran. Only a changed agent file
    needs launchd to unload and load it; when that load fails or is
    interrupted, the agent file that ran before is written back and loaded
    again, and the error is raised.

    Returns "loaded", "restarted", or "" when a running service was left
    as it is. Raises `RuntimeError` when launchd cannot load it.

        start(ctx.shell, agent)                 # "loaded" after `stack down ai`
        start(ctx.shell, agent)                 # "" right after
        start(ctx.shell, agent, changed=True)   # "restarted": it reads its settings again
    """
    path = agent_file(agent.label)
    before = path.read_text() if path.exists() else None
    files_changed = _write_files(agent)
    if not _loaded(run, agent.label):
        _write_agent_file(agent)
        run(shlex.join(["launchctl", "load", str(path)]))
        return "loaded"
    if before != agent.plist:
        _reload(run, agent, before)
        return "restarted"
    if files_changed or changed or not _running(run, agent.label):
        restart(run, agent.label)
        return "restarted"
    return ""


def stop(run: Runner, label: str) -> bool:
    """Stop the service `label` and remove its agent file, so launchd does
    not start it again at the next login. Returns whether there was one to
    stop: a job launchd has, or a file login would load.

    launchd is asked, not the file: a job is stopped by its label
    (`launchctl remove`), also when its file is gone. The file goes only
    after launchd let go of the job. A stop launchd refuses raises
    `RuntimeError` and keeps the file, because a job that runs on without
    its file is one famstack can no longer find.

        stop(ctx.shell, WHISPER)    # True after `stack up ai`, False after
    """
    path = agent_file(label)
    loaded = _loaded(run, label)
    if loaded:
        run(shlex.join(["launchctl", "remove", label]))
    if not path.exists():
        return loaded
    path.unlink()
    return True


def wait_until_stopped(run: Runner, label: str, timeout: float = STOP_SECONDS) -> bool:
    """Wait until the process of `label` has exited: launchd lists the job
    without a PID, or not at all. Returns False when it still runs after
    `timeout` seconds. A stop sends TERM and returns before the process
    ends; whatever takes its port next waits here first.

        stop(ctx.shell, OMLX)
        wait_until_stopped(ctx.shell, OMLX)    # True once oMLX let go of port 42060
    """
    deadline = time.monotonic() + timeout
    while _running(run, label):
        if time.monotonic() >= deadline:
            return False
        time.sleep(STOP_POLL_SECONDS)
    return True


def restart(run: Runner, label: str) -> None:
    """Restart the service `label` in place, so it reads its settings
    again: `launchctl kickstart -k` stops its process and starts it, and
    the job never leaves launchd. Also starts a loaded job whose process
    had ended (famstack's oMLX after it stepped aside for the oMLX app).

        restart(ctx.shell, OMLX)    # "launchctl kickstart -k gui/501/dev.famstack.omlx"
    """
    for command in restart_commands(label):
        run(shlex.join(command))


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
    return {label for label in LABELS if _loaded(run, label) or agent_file(label).exists()}


def shell(command: str) -> str:
    """The runner for a caller without a hook context: runs `command` in a
    shell and returns its output, or raises `RuntimeError` with its error.

        stop(shell, OMLX)
    """
    done = subprocess.run(command, shell=True, capture_output=True, text=True,
                          timeout=LAUNCHCTL_SECONDS)
    if done.returncode != 0:
        raise RuntimeError(f"{command}: {done.stderr.strip() or f'exit {done.returncode}'}")
    return done.stdout


# ── Asking launchd ───────────────────────────────────────────────────────

def _loaded(run: Runner, label: str) -> bool:
    """Whether launchd has the agent: `launchctl list <label>` fails when not."""
    try:
        run(shlex.join(["launchctl", "list", label]))
    except RuntimeError:
        return False
    return True


def _running(run: Runner, label: str) -> bool:
    """Whether the loaded agent's process runs: launchd names its `PID`
    only while it does."""
    try:
        listed = run(shlex.join(["launchctl", "list", label]))
    except RuntimeError:
        return False
    return '"PID"' in (listed or "")


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


def _reload(run: Runner, agent: Agent, before: str | None) -> None:
    """Replace a loaded agent whose file changed: unload it, write the new
    file, load it. When the load fails, or anything ends the run on the
    way, the file `before` is written back and loaded again, so the
    service that ran keeps running, and the error is raised."""
    path = agent_file(agent.label)
    run(shlex.join(["launchctl", "unload", str(path)]))
    try:
        _write_agent_file(agent)
        run(shlex.join(["launchctl", "load", str(path)]))
    except BaseException:
        if before is not None:
            path.write_text(before)
            try:
                run(shlex.join(["launchctl", "load", str(path)]))
            except RuntimeError:
                pass
        raise
