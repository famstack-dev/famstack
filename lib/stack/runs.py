"""runs.py: one run per stacklet, and one run of the stack at a time.

A run is one `stack up`, `down`, `restart` or `destroy` of a stacklet, or a
`stack update`. It holds the stacklet's run lock, `.stack/runs/<id>.lock`,
from its start to its end. A second run of the same stacklet, from any
surface (a terminal, the host API behind the Server room and the dashboard,
the menu bar app), does not wait: it is refused with `AlreadyRunning`,
which says who holds the lock and since when, and the command exits with
`EXIT_ALREADY_RUNNING`.

Runs of different stacklets do not overlap either. After its own locks,
a run takes the stack's lock, `.stack/runs/one-run.lock`, and a run that
finds it held waits in a queue and says so (`waiting`) until the run before
it has ended. The setup page's "Start all three" thus becomes three runs one
after the other, each shown on its tile as waiting until it starts.

    from stack import runs

    with runs.hold(instance_dir, ["photos"], waiting=say):  # refuses a second
        ...start photos...                                  # run of photos,
                                                            # queues behind
                                                            # any other run

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

Why one run at a time. A run that ends a stacklet's first start recreates
core (`stack.cli._refresh_core`), which closes core's card door and stops
the bot runner for seconds. A run of another stacklet that used core in
those seconds failed: memory's migrations write through the card door, and
one that failed held every bot until a later `stack up memory`. The runs
use core at many points (health checks, hooks, migrations), so the runs
themselves are put in a row instead of each of those points.

Why the stacklet's lock comes first. A queued run has begun: it holds its
stacklet's lock, so a second start of the same stacklet is refused at once,
and its run log is open, so the dashboard's tile and the host API, which
waits for a run to begin, see it waiting. A queued run does nothing else,
which is why no run waits for another stacklet's lock: the stacklet locks a
running run does not hold are held by queued runs at most, and waiting for
one of those would wait for ever. A run that starts another stacklet on its
way (a requirement, core's refresh) does so without that stacklet's lock;
the one-run lock already keeps every other run out.

Nesting. `restart` holds the lock across its `down` and its `up`, and
`up all` holds every stacklet it starts. The `down` and `up` inside take the
same locks again, so a lock this process already holds is taken again
without a second `flock` (two open files of one process would refuse each
other). The one-run lock is taken by the outermost `hold` of a process.

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
import time
from collections.abc import Callable, Generator, Iterable
from datetime import datetime
from pathlib import Path
from typing import IO

from . import run_log

__all__ = [
    "EXIT_ALREADY_RUNNING",
    "SURFACE_VARIABLE",
    "AlreadyRunning",
    "hold",
    "is_held",
    "lock_path",
]

# EX_TEMPFAIL of sysexits.h: the command can work later, nothing is broken.
# Its own code, not 1, so a caller can tell "busy" from "failed".
EXIT_ALREADY_RUNNING = 75

SURFACE_VARIABLE = "STACK_RUN_SURFACE"
_DEFAULT_SURFACE = "terminal"

# The stack's lock, which one run at a time holds. A hyphen is in no
# stacklet id, so the name never meets a stacklet's lock or run log.
ONE_RUN = "one-run"

# How often a queued run tries the stack's lock again: one `flock` without
# waiting, cheap enough to ask often, so the next run starts at once.
QUEUE_POLL_SECS = 0.2

# How long a queued run waits behind one run before it says again that it
# waits. The host API ends a run it started once it said nothing for
# 1260 s (core/famstack-api.py, STREAM_SILENCE_SECS); a long first start
# ahead in the queue (an image download) must not end the ones behind it.
QUEUE_REMIND_SECS = 300


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


Waiting = Callable[[dict], contextlib.AbstractContextManager]


@contextlib.contextmanager
def hold(instance_dir: Path, stacklets: Iterable[str],
         waiting: Waiting | None = None) -> Generator[None]:
    """Hold the run locks of `stacklets` and the stack's lock for the
    `with` block; refuse a second run of a stacklet, queue behind any run.

    Takes the stacklet locks in sorted order and does not wait: a lock held
    by another process raises `AlreadyRunning` for that stacklet, and the
    locks taken so far are released. Then the stack's lock (`ONE_RUN`),
    waiting while another run holds it: `waiting(holder)` is entered for as
    long as one holder keeps it, with what that run wrote (its surface,
    action and `stacklets`), and again after `QUEUE_REMIND_SECS`. A lock
    this process holds already is taken again (see "Nesting"). Each newly
    taken stacklet lock gets this run's details, and its stacklet a new run
    log (`stack.run_log`); what is reported in the block, the waiting
    included, goes to the logs of `stacklets`. With no `stacklets` only the
    stack's lock is taken (`stack update`). Creates `.stack/runs/` when it
    is missing.
    """
    stacklets = sorted(set(stacklets))
    with contextlib.ExitStack() as taken:
        for stacklet in stacklets:
            taken.enter_context(_lock(instance_dir, stacklet))
        with run_log.focus(instance_dir, stacklets):
            with _one_run(instance_dir, stacklets, waiting or _quietly):
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
def _lock(instance_dir: Path, stacklet: str) -> Generator[None]:
    """One stacklet's lock, taken once per process and counted when nested;
    its run log runs from the lock's taking to its letting go."""
    path = lock_path(instance_dir, stacklet).resolve()
    if path in _held:
        with _nested(path):
            yield
        return

    details = _this_run()
    handle = _acquire(path, stacklet, details)
    _held[path] = [handle, 1]
    run_log.begin(instance_dir, stacklet, details)
    completed = False
    try:
        yield
        completed = True
    finally:
        run_log.end(instance_dir, stacklet, completed)    # before the lock goes
        del _held[path]
        handle.close()          # closing the file releases the flock


@contextlib.contextmanager
def _one_run(instance_dir: Path, stacklets: list[str], waiting: Waiting) -> Generator[None]:
    """The stack's lock, taken once per process: in the queue until the
    run that holds it has ended, then held for the `with` block."""
    path = lock_path(instance_dir, ONE_RUN).resolve()
    if path in _held:
        with _nested(path):
            yield
        return

    handle = _queue(path, {**_this_run(), "stacklets": stacklets}, waiting)
    _held[path] = [handle, 1]
    try:
        yield
    finally:
        del _held[path]
        handle.close()


@contextlib.contextmanager
def _nested(path: Path) -> Generator[None]:
    """A lock this process holds, taken again: only counted."""
    _held[path][1] += 1
    try:
        yield
    finally:
        _held[path][1] -= 1


def _queue(path: Path, details: dict, waiting: Waiting) -> IO[str]:
    """Take the lock at `path`, waiting behind whoever holds it; the open file.

    While one holder keeps it, `waiting(holder)` is entered once; a new
    holder, or `QUEUE_REMIND_SECS` behind the same one, enters it again.
    The lock is asked for without blocking, every `QUEUE_POLL_SECS`, so the
    waiting run can say who it waits for.
    """
    while True:
        try:
            return _acquire(path, ONE_RUN, details)
        except AlreadyRunning as busy:
            holder = busy.holder
        with waiting(holder):
            handle = _wait_behind(path, holder, details)
        if handle is not None:
            return handle


def _wait_behind(path: Path, holder: dict, details: dict) -> IO[str] | None:
    """The lock once it is ours; None when another run holds it now, or
    when `QUEUE_REMIND_SECS` passed behind `holder`."""
    deadline = time.monotonic() + QUEUE_REMIND_SECS
    while time.monotonic() < deadline:
        time.sleep(QUEUE_POLL_SECS)
        try:
            return _acquire(path, ONE_RUN, details)
        except AlreadyRunning as busy:
            if busy.holder != holder:
                return None
    return None


def _quietly(_holder: dict) -> contextlib.AbstractContextManager:
    return contextlib.nullcontext()


def _acquire(path: Path, name: str, details: dict) -> IO[str]:
    """Open and lock `path` without waiting, write this run's `details`; the
    open file. Raises `AlreadyRunning` for `name` while another process
    holds it.

    The file is opened for appending, never truncated before the lock is
    ours: truncating first would erase the holder's details that a refused
    run is about to read.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        holder = _read_holder(handle)
        handle.close()
        raise AlreadyRunning(name, holder) from None
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
