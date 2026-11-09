"""runs.py: one run per stacklet at a time.

A run is one `stack up`, `down`, `restart` or `destroy` of a stacklet. It
holds the stacklet's run lock, `.stack/runs/<id>.lock`, from its start to
its end. A second run of the same stacklet, from any surface (a terminal,
the host API behind the Server room and the dashboard, the menu bar app),
does not wait: it is refused with `AlreadyRunning`, which says who holds
the lock and since when, and the command exits with `EXIT_ALREADY_RUNNING`.

    from stack import runs

    with runs.hold(instance_dir, ["photos"]):        # refuses a second run
        ...start photos...

    with runs.wait_for(instance_dir, "core"):        # waits for core's run
        ...recreate core...

    try:
        with runs.hold(instance_dir, ["photos"]): ...
    except runs.AlreadyRunning as busy:
        busy.stacklet, busy.holder   # "photos", {"surface": "terminal",
                                     #   "started": "2026-11-09T18:40:12+01:00",
                                     #   "pid": 4711, "action": "up"}

Why a file lock. Nothing locked `stack up` before: only the dashboard
joined a second start onto its own job, inside one process. Two first runs
of one stacklet then both ran on_configure, on_install and compose up. The
lock is an advisory `fcntl.flock`, held by the open file of the running
process. The kernel releases it when the process ends in any way: success,
failure, Ctrl-C, a crash, a kill. So there is no stale lock to clean up and
no guess whether a recorded pid is still alive.

What the lock file holds. After the lock is taken, the holder writes one
JSON object into the file: its surface, start time, pid and action (`up`,
`down`, `restart`, `destroy`). Only the action, never the command line: an
`--answer` on it can carry a password. A
refused run reads it to say who runs the stacklet. The content outlives
the run, but it is read only while somebody holds the lock, so an old
content is never shown. A run refused in the moment between the holder's
lock and its write shows no details.

The surface. A caller that is not a person at a terminal sets
`STACK_RUN_SURFACE` in the command's environment (for example
`dashboard`). Without it the run counts as started from the terminal.

Nesting. `restart` holds the lock across its `down` and its `up`, and
`up all` holds every stacklet it starts. The `down` and `up` inside take the
same locks again, so a lock this process already holds is taken again
without a second `flock` (two open files of one process would refuse each
other). A run that has to start another stacklet on its way (core's
refresh at the end of `stack up`) waits for that stacklet's run with
`wait_for` instead of refusing.

Where it fits: `stack.cli.CLI` takes the locks in `up`, `down` and
`destroy`; `stack.cli.main` turns `AlreadyRunning` into the message and the
exit code. Each run also writes its events to the stacklet's run log,
`.stack/runs/<id>.jsonl`, opened here after the lock is taken and closed
before it is let go (`stack.run_log`). Design: the install experience's
"Run lock". Not here: following a run from another surface
(`stack.live.follow`, which asks `is_held` whether a run is over).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
from collections.abc import Generator, Iterable
from datetime import datetime
from pathlib import Path
from typing import IO

from . import run_log

__all__ = [
    "EXIT_ALREADY_RUNNING",
    "SURFACE_VARIABLE",
    "AlreadyRunning",
    "hold",
    "wait_for",
    "is_held",
    "lock_path",
]

# EX_TEMPFAIL of sysexits.h: the command can work later, nothing is broken.
# Its own code, not 1, so a caller can tell "busy" from "failed".
EXIT_ALREADY_RUNNING = 75

SURFACE_VARIABLE = "STACK_RUN_SURFACE"
_DEFAULT_SURFACE = "terminal"


class AlreadyRunning(Exception):
    """A run of `stacklet` is going on in another process.

    `holder` is what that run wrote into its lock file: `surface`,
    `started` (ISO 8601), `pid` and `action`, or an empty dict when it had
    not written yet. `str()` is the message for people:

        >>> busy = AlreadyRunning("photos", {"surface": "dashboard",
        ...     "started": "2026-11-09T18:40:12+01:00", "pid": 4711,
        ...     "action": "up"})
        >>> print(busy)
        photos is already starting (from the dashboard, since 18:40, pid 4711)
    """

    def __init__(self, stacklet: str, holder: dict):
        self.stacklet = stacklet
        self.holder = holder
        super().__init__(_message(stacklet, holder))

    def hints(self) -> list[str]:
        """What the refused person can do: wait, or follow the containers."""
        return [
            "Wait until that run ends, then try again.",
            f"Follow its containers meanwhile: stack logs {self.stacklet}",
        ]

    def as_result(self) -> dict:
        """The refusal as a command result, for `--events` and JSON callers."""
        return {"error": str(self), "stacklet": self.stacklet,
                "running": self.holder, "hints": self.hints()}


def lock_path(instance_dir: Path, stacklet: str) -> Path:
    """The run lock file of `stacklet`: `<instance>/.stack/runs/<id>.lock`."""
    return Path(instance_dir) / ".stack" / "runs" / f"{stacklet}.lock"


@contextlib.contextmanager
def hold(instance_dir: Path, stacklets: Iterable[str]) -> Generator[None]:
    """Hold the run locks of `stacklets` for the `with` block, or refuse.

    Takes the locks in sorted order and does not wait: a lock held by
    another process raises `AlreadyRunning` for that stacklet, and the
    locks taken so far are released. A lock this process holds already is
    taken again (see "Nesting"). Each newly taken lock gets this run's
    details, and its stacklet a new run log (`stack.run_log`); what is
    reported in the block goes to the logs of `stacklets`. Creates
    `.stack/runs/` when it is missing.
    """
    stacklets = sorted(set(stacklets))
    with contextlib.ExitStack() as taken:
        for stacklet in stacklets:
            taken.enter_context(_lock(instance_dir, stacklet, wait=False))
        with run_log.focus(instance_dir, stacklets):
            yield


@contextlib.contextmanager
def wait_for(instance_dir: Path, stacklet: str) -> Generator[None]:
    """Hold the run lock of `stacklet` for the `with` block, waiting for it.

    For a run that starts another stacklet on its way: it waits until that
    stacklet's own run ends, instead of refusing. Returns at once when the
    lock is free or this process holds it.
    """
    with _lock(instance_dir, stacklet, wait=True):
        yield


def is_held(instance_dir: Path, stacklet: str) -> bool:
    """Whether a run of `stacklet` holds its lock now, in any process.

    For a reader of the run log (`stack.live.follow`): a log without a
    `result` whose lock is free belongs to a run that stopped (Ctrl-C, a
    crash, a kill). A lock file that does not exist is free.

    `flock` cannot be asked who holds a lock, so this tries a shared lock
    without waiting and lets it go at once. Held by a run, the try fails
    and changes nothing. Free, it holds the lock for that moment: a run
    that starts in the same moment is refused as if another run held it.
    Readers ask only about a log that has no result, so that moment comes
    once for each run that stopped.
    """
    path = lock_path(instance_dir, stacklet).resolve()
    if path in _held:
        return True
    try:
        handle = open(path, encoding="utf-8")
    except FileNotFoundError:
        return False
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


# ── Taking one lock ───────────────────────────────────────────────────────

# The locks this process holds: lock path -> [open file, depth].
_held: dict[Path, list] = {}


@contextlib.contextmanager
def _lock(instance_dir: Path, stacklet: str, wait: bool) -> Generator[None]:
    """One stacklet's lock, taken once per process and counted when nested."""
    path = lock_path(instance_dir, stacklet).resolve()
    if path in _held:
        _held[path][1] += 1
        try:
            yield
        finally:
            _held[path][1] -= 1
        return

    details = _this_run()
    handle = _acquire(path, stacklet, wait, details)
    _held[path] = [handle, 1]
    # A run (`hold`) writes its log; a wait for another stacklet's run
    # (core's refresh) is no run of that stacklet and leaves its log alone.
    logged = not wait
    if logged:
        run_log.begin(instance_dir, stacklet, details)
    completed = False
    try:
        yield
        completed = True
    finally:
        if logged:
            run_log.end(instance_dir, stacklet, completed)    # before the lock goes
        del _held[path]
        handle.close()          # closing the file releases the flock


def _acquire(path: Path, stacklet: str, wait: bool, details: dict) -> IO[str]:
    """Open and lock `path`, write this run's `details`; the open file.

    The file is opened for appending, never truncated before the lock is
    ours: truncating first would erase the holder's details that a refused
    run is about to read.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        holder = _read_holder(handle)
        handle.close()
        raise AlreadyRunning(stacklet, holder) from None
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps(details))
    handle.flush()
    return handle


def _read_holder(handle: IO[str]) -> dict:
    """The details the holder wrote, or {} when it has not written yet."""
    handle.seek(0)
    try:
        holder = json.loads(handle.read() or "{}")
    except ValueError:
        return {}
    return holder if isinstance(holder, dict) else {}


def _this_run() -> dict:
    """This process's details, as a refused run shows them."""
    return {
        "surface": os.environ.get(SURFACE_VARIABLE) or _DEFAULT_SURFACE,
        "started": datetime.now().astimezone().isoformat(timespec="seconds"),
        "pid": os.getpid(),
        "action": sys.argv[1] if len(sys.argv) > 1 else "",
    }


# ── The message ───────────────────────────────────────────────────────────

def _message(stacklet: str, holder: dict) -> str:
    """One line: what runs, from where, since when, which process."""
    details = []
    if surface := holder.get("surface"):
        details.append(f"from the {surface}")
    if started := holder.get("started"):
        details.append(f"since {_clock(started)}")
    if pid := holder.get("pid"):
        details.append(f"pid {pid}")
    text = f"{stacklet} is already {_DOING.get(holder.get('action', ''), 'busy')}"
    return f"{text} ({', '.join(details)})" if details else text


# What the holder's action does to the stacklet, as the message says it.
_DOING = {"up": "starting", "down": "stopping", "restart": "restarting",
          "destroy": "being removed"}


def _clock(started: str) -> str:
    """`HH:MM` of an ISO time, or the text as it is when it does not parse."""
    try:
        return datetime.fromisoformat(started).strftime("%H:%M")
    except ValueError:
        return started
