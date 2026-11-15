"""launchd: ask it about a job, stop a job, load an agent, and trust only its answer.

    from stack import launchd

    launchd.loaded(ctx.shell, "dev.famstack.api")          # True while launchd has the job
    launchd.stop(ctx.shell, "dev.famstack.api")            # removed, and launchd let it go
    launchd.load(ctx.shell, agents / "dev.famstack.api.plist")   # loaded, or RuntimeError
    launchd.wait_until_stopped(ctx.shell, label)           # its process has exited

The stack runs its services on the Mac, outside Docker, as LaunchAgents:
core's host API (`stacklets/core/hooks`) and the AI's oMLX and Whisper
(`stacklets/ai/host_services.py`). This module is the one place that
stops and loads such a job, because launchd's exit codes do not say what
happened:

- `launchctl remove` and `unload` return at once, and launchd lists the
  job until its process has ended, about a second for oMLX.
- A `launchctl load` of the same label in that moment prints `Load
  failed: 5: Input/output error`, loads nothing, and exits 0.

A restart is a stop and a load within the same second, so it left the
service down while every line said it runs. Here `stop` returns only once
launchd no longer lists the job, and `load` asks launchd afterwards
whether it has the job and raises with launchd's own words when not.

Commands go through a runner, `run(command, timeout=seconds) -> stdout`,
which raises `RuntimeError` when the command fails or runs out of time: a
hook passes `ctx.shell`. Every `launchctl` call gets `LAUNCHCTL_SECONDS`,
because launchd answers within a second, and a `launchctl` that hangs
must not hold a stop for the shell's own limit of twenty minutes. Every
path is quoted for the shell, so a home such as `/Users/o'brien` stays one
path.

What it does not do: write agent files, decide what a job runs, or wait
for a service to answer on its port. Its callers do that.
"""

from __future__ import annotations

import shlex
import time
from collections.abc import Callable
from pathlib import Path

__all__ = ["listed", "loaded", "running", "stop", "load", "wait_until_stopped",
           "STOP_SECONDS", "NOT_LISTED"]

Runner = Callable[..., str]

# How long one `launchctl` call may take. launchd answers within a second.
LAUNCHCTL_SECONDS = 10

# How long a stopped job may take to leave launchd: oMLX unloads its
# model and ends within seconds of TERM; launchd kills it after 20 s.
STOP_SECONDS = 30
# How often a stop asks launchd whether it let the job go. `launchctl
# list` is cheap, and launchd usually lets go within a second.
LEAVE_POLL_SECONDS = 0.1
# How often `wait_until_stopped` asks whether the process still runs.
STOP_POLL_SECONDS = 0.5
# `launchctl list <label>` exits 113 when launchd has no such job. The
# list command turns that one exit into this line on its output, so a
# runner that raises on every failure still tells it apart from the
# others, which stay failures.
NO_SUCH_JOB_EXIT = 113
NOT_LISTED = "launchd lists no such job"


# ── Asking launchd ───────────────────────────────────────────────────────

def listed(run: Runner, label: str) -> str | None:
    """What launchd says about the job `label` (`launchctl list <label>`),
    or None when it has no such job. A job that is leaving is still
    listed, until its process ended. Raises `RuntimeError` with
    launchd's words when `launchctl list` fails for any other reason:
    that says nothing about the job, so it is never taken for a missing
    one.

        listed(ctx.shell, "dev.famstack.api")   # '{ "PID" = 4242; ... };'
    """
    command = shlex.join(["launchctl", "list", label])
    said = run(f"{command} || {{ s=$?; [ $s -eq {NO_SUCH_JOB_EXIT} ] && echo {shlex.quote(NOT_LISTED)}"
               f" || {{ echo {shlex.quote(command)} exited $s >&2; exit $s; }}; }}",
               timeout=LAUNCHCTL_SECONDS) or ""
    return None if NOT_LISTED in said else said


def loaded(run: Runner, label: str) -> bool:
    """Whether launchd has the job `label`, leaving or not (`listed`).

        loaded(ctx.shell, "dev.famstack.omlx")    # False after `stop`
    """
    return listed(run, label) is not None


def running(run: Runner, label: str) -> bool:
    """Whether the process of the job `label` runs: launchd names its
    `PID` only while it does (`listed`).

        running(ctx.shell, "dev.famstack.omlx")   # False while oMLX steps aside
    """
    return '"PID"' in (listed(run, label) or "")


# ── Stopping and loading ─────────────────────────────────────────────────

def stop(run: Runner, label: str) -> bool:
    """Stop the job `label` by its label and return once launchd no longer
    lists it. Returns whether launchd had it.

    A load of the same label before launchd let go fails without an
    error, so whatever loads next depends on this wait. launchd refuses a
    `remove` of a job that is already leaving, after an earlier stop, so a
    refused `remove` is no failure as such: the stop waits all the same.
    Raises `RuntimeError` when launchd still lists the job after
    `STOP_SECONDS`, with launchd's reason when it refused the `remove`.
    The agent file is left alone: the caller decides whether it goes.

        stop(ctx.shell, "dev.famstack.api")    # True after `stack up core`
        stop(ctx.shell, "dev.famstack.api")    # False right after
    """
    if not loaded(run, label):
        return False
    try:
        run(shlex.join(["launchctl", "remove", label]), timeout=LAUNCHCTL_SECONDS)
        refused = ""
    except RuntimeError as e:
        refused = f" (launchd refused the stop: {e})"
    deadline = time.monotonic() + STOP_SECONDS
    while loaded(run, label):
        if time.monotonic() >= deadline:
            raise RuntimeError(f"launchd still runs {label} {STOP_SECONDS} s after it was "
                               f"stopped{refused}")
        time.sleep(LEAVE_POLL_SECONDS)
    return True


def load(run: Runner, agent_file: Path) -> None:
    """Load `agent_file`, whose name is its label (`<label>.plist`), and
    check that launchd has the job. Raises `RuntimeError` with launchd's
    output when it does not: `launchctl load` exits 0 also when it
    refused the file, and says why only in its output.

    Refuses, with `RuntimeError`, a label launchd still lists: a job that
    is leaving stays listed after a refused load, so only a label that
    was free before can show that this load worked. Stop it first.

        load(ctx.shell, Path.home() / "Library/LaunchAgents/dev.famstack.api.plist")
    """
    label = agent_file.stem
    # A failing `launchctl list` raises here with launchd's words: then
    # neither this check nor the one after the load could be trusted.
    if loaded(run, label):
        raise RuntimeError(f"launchd still has {label}; stop it before loading {agent_file}")
    command = shlex.join(["launchctl", "load", str(agent_file)])
    said = (run(f"{command} 2>&1", timeout=LAUNCHCTL_SECONDS) or "").strip()
    if not loaded(run, label):
        raise RuntimeError(f"launchd did not load {agent_file}: {said or 'it gave no reason'}")


def wait_until_stopped(run: Runner, label: str, timeout: float = STOP_SECONDS) -> bool:
    """Wait until the process of `label` has exited: launchd lists the job
    without a PID, or not at all. Returns False when it still runs after
    `timeout` seconds. For a job stopped by another tool, such as
    Homebrew's `brew services stop`; `stop` waits by itself.

        wait_until_stopped(ctx.shell, "dev.famstack.omlx")   # True once port 42060 is free
    """
    deadline = time.monotonic() + timeout
    while running(run, label):
        if time.monotonic() >= deadline:
            return False
        time.sleep(STOP_POLL_SECONDS)
    return True
