"""Follow a stacklet's runs on the Mac, from their run logs.

Every run of a stacklet (`stack up`, `down`, `restart`, `destroy`), from
any surface, writes its events to the stacklet's run log and holds its run
lock until it ends (`stack.run_log`, `stack.runs`). This module reads both,
for the host API, which runs on the Mac:

    for event in follow(instance_dir, "photos"):      # the current or last run
        print(event.type, event.data)                 # run, step.*, warning, result

    for run in starts(instance_dir, heartbeat_secs=30):
        ...                                           # a `run` event as each run starts; None while idle

    current(instance_dir, "photos")                   # {"stacklet": "photos", "surface": ..., "pid": ...}

`follow` yields the events of one run as the run writes them, from
`after` on, and always ends with exactly one `result`: the run's own, or
one written here when the run stopped without one. A reader never waits
for a result that cannot come. `starts` reports the runs going on when it
is called, then each run that starts after, so a screen can follow a run
that it did not start.

Why it works this way:

- **The log is the run's state** (ADR-020, principle 3). A reader that
  went away, or a tools server that restarted, reads the log again from
  the start, or from the last event id it saw. The run never waits for a
  reader.
- **A free lock ends a run without a result.** A run that is killed,
  crashes or is stopped with Ctrl-C writes no result, but the kernel lets
  its lock go. A log without a result whose lock is free is a run that
  stopped (`runs.is_held`).
- **A run is known by its `run` event:** the stacklet, its start time and
  its process. A new run of the stacklet empties the log and writes a new
  one; a reader that sees another `run` event first knows its own run is
  over.
- **It reads files, it does not wait on them.** A follower reads its log
  every `POLL_SECS` while the run goes on; `starts` reads the first line
  of each log every `WATCH_SECS`. Both are a few small local files, and
  nothing reads while nobody follows.

Where it fits: the host API (`stacklets/core/famstack-api.py`) serves
`follow` and `runs` as streams, and its streamed `up` starts a run and then
follows it with `follow`. The tools server follows what the host API
streams (`stack.live.watch`).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

from .. import run_log, runs
from .envelope import RESULT, RUN, Event

# How often a follower reads the log of a run that goes on. A step shows on
# a screen at most this late.
POLL_SECS = 0.25
# How often `starts` looks for a run that has started.
WATCH_SECS = 1.0

# A result written here, not by the run, carries `stopped`: "ended" for a
# run that stopped without a result, "silent" for one that said nothing
# for `silence_secs`. Not a field for screens (`result_for_screens`): the
# host API reads it to say more about a run it started.
STOPPED, ENDED, SILENT = "stopped", "ended", "silent"


def follow(instance_dir: Path, stacklet: str, *, after: int = 0,
           silence_secs: float | None = None) -> Iterator[Event]:
    """The events of `stacklet`'s current or last run with an id above
    `after`, as the run writes them, a `result` last.

    The result is the run's own when it wrote one. Otherwise it is written
    here, with `stopped`: when the lock is free (the run stopped), when
    another run replaced the log (so this one is over), and, with
    `silence_secs`, when the run wrote nothing for that long. A stacklet
    that never ran gets a result with an error at once.
    """
    path = run_log.log_path(instance_dir, stacklet)
    run: dict | None = None
    sent = after
    heard = time.monotonic()
    while True:
        events = _read(path)
        if events and run is None:
            run = events[0].data
        if events and events[0].data != run:
            yield _written(sent, stacklet, ENDED, "the run stopped before it finished")
            return
        for event in events:
            if _number(event) > sent:
                yield event
                sent, heard = _number(event), time.monotonic()
                if event.type == RESULT:
                    return
        if not runs.is_held(instance_dir, stacklet):
            yield from _last_words(path, run, sent, stacklet)
            return
        if silence_secs is not None and time.monotonic() - heard > silence_secs:
            yield _written(sent, stacklet, SILENT, f"nothing heard from the run for {silence_secs} s")
            return
        time.sleep(POLL_SECS)


def current(instance_dir: Path, stacklet: str) -> dict | None:
    """The `run` event's data of `stacklet`'s current or last run: the
    stacklet, surface, start time, pid and action. None without a log."""
    first = _first(run_log.log_path(instance_dir, stacklet))
    return first.data if first else None


def starts(instance_dir: Path, *, heartbeat_secs: float) -> Iterator[Event | None]:
    """Each run's `run` event as the run starts; None after `heartbeat_secs`
    with nothing to report, so the caller can tell its reader it is alive.

    First the runs going on now (a log without a result, its lock held),
    then every run that starts after, also one that ended between two looks.
    Each run once. Never ends; the caller stops reading.
    """
    known: dict[str, dict] = {}
    for path in _logs(instance_dir):
        if (first := _first(path)) is None:
            continue
        known[path.stem] = first.data
        if _going_on(instance_dir, path):
            yield first
    quiet = 0.0
    while True:
        time.sleep(WATCH_SECS)
        quiet += WATCH_SECS
        for path in _logs(instance_dir):
            first = _first(path)
            if first is None or known.get(path.stem) == first.data:
                continue
            known[path.stem] = first.data
            quiet = 0.0
            yield first
        if quiet >= heartbeat_secs:
            quiet = 0.0
            yield None


# ── Reading a log ─────────────────────────────────────────────────────────

def _read(path: Path) -> list[Event]:
    """The events in a log so far; [] when there is none yet.

    A line the run is still writing does not parse and is skipped: the
    next read has it whole. A log that does not begin with a `run` event
    is in the moment between a new run emptying it and writing that event.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    events = [event for line in text.splitlines()
              if (event := Event.from_line(line)) and isinstance(event.id, int)]
    return events if events and events[0].type == RUN else []


def _number(event: Event) -> int:
    """A logged event's id: the run numbers its events from 1 (`_read` keeps only those)."""
    return event.id if isinstance(event.id, int) else 0


def _first(path: Path) -> Event | None:
    """The `run` event a log begins with, or None."""
    try:
        with path.open(encoding="utf-8") as log:
            event = Event.from_line(log.readline())
    except FileNotFoundError:
        return None
    return event if event and event.type == RUN else None


def _logs(instance_dir: Path) -> list[Path]:
    return sorted(run_log.log_path(instance_dir, "_").parent.glob("*.jsonl"))


def _going_on(instance_dir: Path, path: Path) -> bool:
    """Whether the run in a log goes on: no result yet, and its lock held."""
    if any(event.type == RESULT for event in _read(path)):
        return False
    return runs.is_held(instance_dir, path.stem)


def _last_words(path: Path, run: dict | None, sent: int, stacklet: str) -> Iterator[Event]:
    """The end of a run whose lock is free.

    The run writes its result before it lets the lock go, so a read after
    the lock was seen free has it, if the run wrote one.
    """
    events = _read(path)
    if run is None and not events:
        yield _written(sent, stacklet, ENDED, f"{stacklet} has not run yet")
        return
    if events and events[0].data == run:
        for event in events:
            if _number(event) > sent:
                yield event
                sent = _number(event)
                if event.type == RESULT:
                    return
    yield _written(sent, stacklet, ENDED, "the run stopped before it finished")


def _written(sent: int, stacklet: str, how: str, error: str) -> Event:
    """A result written here for a run that has none."""
    return Event.now(sent + 1, RESULT, {"error": error, "stacklet": stacklet, STOPPED: how})
