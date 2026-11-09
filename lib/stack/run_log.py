"""run_log.py: each stacklet's current or last run, as events on the Mac.

A run (one `stack up`, `down`, `restart` or `destroy` of a stacklet, see
`stack.runs`) reports its steps to the surface that started it: the
terminal, or the stdout of `stack up --events` that the host API relays.
The run log keeps the same events in a file, `.stack/runs/<id>.jsonl`, so
another surface can follow a run it did not start.

    .stack/runs/photos.lock     who holds the run (stack.runs)
    .stack/runs/photos.jsonl    what the run did so far (this module)

One line per event, in ADR-020's envelope (`stack.live.envelope`), ids
counting from 1. A run of photos from the terminal leaves:

    {"id": 1, "type": "run", ..., "data": {"stacklet": "photos",
        "surface": "terminal", "started": "2026-11-09T18:40:12+01:00",
        "pid": 4711, "action": "up"}}
    {"id": 2, "type": "step.started", ..., "data": {"text": "Pulling images"}}
    {"id": 3, "type": "step.done", ..., "data": {"text": "Pulling images", "started": 2}}
    {"id": 4, "type": "result", ..., "data": {"ok": true, "stacklet": "photos", ...}}

How it is used. `stack.runs` opens a stacklet's log when it takes the run
lock and closes it before it lets the lock go; the command line reports
through `RunLogOutput`, which copies what the terminal shows into the logs
of the run going on; the command records the run's result:

    stck.output = RunLogOutput(TerminalOutput())    # stack.cli, once
    with runs.hold(instance_dir, ["photos"]):       # begin() ... end()
        stck.output.step("Pulling images")          # terminal and log
        record_result(instance_dir, "photos", result)

Why it works this way:

- **Only the holder of the lock writes.** The log is opened, and emptied,
  after the lock is taken, so a refused second run never touches it, and
  the file always holds the current run or the last one. Older runs are
  not kept.
- **A crash leaves no `result`.** The result is written when the run's
  block ends without an exception. A run that ends in Ctrl-C, a crash or a
  kill leaves a log without one; the free lock tells a reader the run is
  over.
- **A screen may read it.** Steps and warnings are words for people and
  never carry a secret (ADR-020). The result goes through
  `result_for_screens`, so the rendered env, the hints and the answers of
  `--answer` stay out of the file.
- **No run, no writes.** Without an open log `RunLogOutput` only passes its
  calls on, so a stacklet that never ran has no log.

Which log an event goes to. A run that holds several stacklets (`up all`,
`restart a b`) writes into the logs of the stacklets its innermost `hold`
names. The many-stacklet commands take each stacklet's lock again around
its own start and stop (a lock held already is taken again at no cost),
so photos' steps land in photos' log and core's in core's. What such a
command reports outside those blocks lands in every log it holds.

Where it fits: `stack.runs` calls `begin`, `end` and `focus`;
`stack.cli` installs `RunLogOutput` and calls `record_result`. Design:
the install experience's "Run log". Not here: following a log (the host
API's `follow`, the dashboard, the Server room).
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator, Iterable
from pathlib import Path
from typing import IO

from .live.envelope import RUN
from .output import EventOutput, SilentOutput

__all__ = [
    "RunLogOutput",
    "log_path",
    "record_result",
    "begin",
    "end",
    "focus",
]


class RunLogOutput:
    """Reports through `inner`, and into the logs of the run going on.

    Every call reaches `inner` as it is, so the terminal shows what it
    always showed. With no run going on in this process it does nothing
    else. Inside one, each step, warning, error and spinner is written to
    the logs `focus` names, as `EventOutput` writes it to stdout.

        output = RunLogOutput(TerminalOutput())
        output.step("Rendering environment")   # stderr, and with a run
        # going on: {"id": 2, "type": "step.done", ...} in its log
    """

    def __init__(self, inner):
        self.inner = inner

    def debug(self, msg: str) -> None:
        self.inner.debug(msg)

    def step(self, msg: str) -> None:
        self.inner.step(msg)
        for log in _in_focus():
            log.events.step(msg)

    def warn(self, msg: str) -> None:
        self.inner.warn(msg)
        for log in _in_focus():
            log.events.warn(msg)

    def error(self, msg: str) -> None:
        self.inner.error(msg)
        for log in _in_focus():
            log.events.error(msg)

    def flush(self) -> None:
        self.inner.flush()

    def spinner(self, msg: str):
        return _Spinners([self.inner.spinner(msg),
                          *(log.events.spinner(msg) for log in _in_focus())])


def log_path(instance_dir: Path, stacklet: str) -> Path:
    """The run log of `stacklet`: `<instance>/.stack/runs/<id>.jsonl`."""
    return Path(instance_dir) / ".stack" / "runs" / f"{stacklet}.jsonl"


def record_result(instance_dir: Path, stacklet: str, result: dict) -> None:
    """Remember `result` as the run's result, written when the run ends.

    The last one recorded wins: `restart` records its `down`, then its
    `up`. Does nothing when this process runs no log of `stacklet`.
    """
    if log := _open.get(_key(instance_dir, stacklet)):
        log.result = result


def begin(instance_dir: Path, stacklet: str, details: dict) -> None:
    """Start the log of a run that has just taken `stacklet`'s lock.

    Empties the file (or creates it, with `.stack/runs/`) and writes the
    `run` event: the stacklet and the lock's `details` (surface, started,
    pid, action). Call it only while holding the lock.
    """
    path = _key(instance_dir, stacklet)
    path.parent.mkdir(parents=True, exist_ok=True)
    _open[path] = _Log(open(path, "w", encoding="utf-8"))
    _open[path].events.emitter.emit(RUN, {"stacklet": stacklet, **details})


def end(instance_dir: Path, stacklet: str, completed: bool) -> None:
    """Close the log of `stacklet`'s run, before its lock is let go.

    `completed` says the run's block ended without an exception: then the
    recorded result is written last, as far as a screen may see it. A run
    that did not complete, or recorded none, ends without `result`.
    """
    log = _open.pop(_key(instance_dir, stacklet), None)
    if not log:
        return
    if completed and log.result is not None:
        log.events.result(log.result)
    log.file.close()


@contextlib.contextmanager
def focus(instance_dir: Path, stacklets: Iterable[str]) -> Generator[None]:
    """Send what is reported in the `with` block to these stacklets' logs.

    Nests: the innermost block wins, and the outer one is back when it
    ends. Stacklets without an open log in this process are left out.
    """
    logs = [log for sid in stacklets
            if (log := _open.get(_key(instance_dir, sid)))]
    _focus.append(logs)
    try:
        yield
    finally:
        _focus.pop()


# ── The open logs ─────────────────────────────────────────────────────────

class _Log:
    """One open run log: its file, the events written to it, the result."""

    def __init__(self, file: IO[str]):
        self.file = file
        # Its own EventOutput, so its ids count from 1 whatever stdout has.
        self.events = EventOutput(SilentOutput(), file)
        self.result: dict | None = None


# The logs this process writes, by path, and the stack of `focus` blocks.
_open: dict[Path, _Log] = {}
_focus: list[list[_Log]] = []


def _key(instance_dir: Path, stacklet: str) -> Path:
    return log_path(instance_dir, stacklet).resolve()


def _in_focus() -> list[_Log]:
    return _focus[-1] if _focus else []


class _Spinners:
    """Several spinners as one: the terminal's and one per log."""

    def __init__(self, spinners: list):
        self.spinners = spinners

    def __enter__(self):
        for spinner in self.spinners:
            spinner.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        for spinner in self.spinners:
            spinner.__exit__(exc_type, exc, tb)

    def fail(self, hint=None):
        for spinner in self.spinners:
            spinner.fail(hint)
