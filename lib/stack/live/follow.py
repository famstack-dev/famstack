"""Follow a command that reports itself as events, while it runs.

`follow` starts a command that writes events on stdout, one line each
(`stack up <id> --events`), and yields each event as the command writes
it. It always ends with exactly one `result`: the command's own, or one
written here when the command fell over, ended without one, or said
nothing for `silence_secs`, so a reader never waits for one that cannot
come. The host API relays a streamed `stack up` with it
(`stacklets/core/famstack-api.py`).

    for event in follow(["./stack", "up", "photos", "--events"], cwd=root, silence_secs=1260):
        print(event.type, event.data)

Silence ends a run, not its length: one that keeps reporting may take as
long as it needs. A command that falls silent is ended together with
everything it started, since it runs in its own session. A reader that
stops early leaves the command running; it finishes on its own.
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

from .envelope import RESULT, Event

# How much of the command's stderr a result written here carries: the end
# of a traceback says why the run fell over, the rest is progress.
STDERR_TAIL_LINES = 5

# How a run ended, as `_forward` saw it: with its result, with its stdout
# closed before one, or with nothing written for the silence limit.
_GOT_RESULT, _CLOSED, _SILENT = "result", "closed", "silent"


def follow(argv: list[str], *, cwd: str | Path, silence_secs: float) -> Iterator[Event]:
    """Run `argv` in `cwd` and yield the events it writes, a `result` last."""
    what = " ".join([Path(argv[0]).name, *argv[1:]])
    with tempfile.TemporaryFile() as stderr:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=stderr,
            encoding="utf-8", errors="replace",
            cwd=str(cwd), start_new_session=True,
        )
        ended, last_id = yield from _forward(proc.stdout, silence_secs)
        if ended == _SILENT:
            _end(proc)
        proc.wait()
        if ended == _GOT_RESULT:
            return
        error = (f"{what}: stopped hearing from it for {silence_secs} s, so ended it"
                 if ended == _SILENT else f"{what} ended without a result (exit {proc.returncode})")
        yield Event.now(last_id + 1, RESULT, {"error": error, "output": _tail(stderr)})


def _forward(stream, silence_secs: float):
    """Yield each event on `stream` up to the run's `result`; return how
    the run ended and the last event id.

    A reader thread hands the lines over, so waiting for the next one can
    time out: silence is how a hung run shows. A line that is not an event
    is skipped; the command promises there are none.
    """
    lines: queue.Queue[str | None] = queue.Queue()
    threading.Thread(target=_pump, args=(stream, lines), daemon=True).start()
    last_id = 0
    while True:
        try:
            line = lines.get(timeout=silence_secs)
        except queue.Empty:
            return _SILENT, last_id
        if line is None:
            return _CLOSED, last_id
        if (event := Event.from_line(line)) is None:
            continue
        yield event
        last_id = event.id if isinstance(event.id, int) else last_id
        if event.type == RESULT:
            return _GOT_RESULT, last_id


def _pump(stream, lines: queue.Queue) -> None:
    """Put each line of `stream` on `lines`, then None once it closes."""
    for line in stream:
        lines.put(line)
    lines.put(None)


def _end(proc: subprocess.Popen) -> None:
    """Stop a run and everything in its session: politely, then for good."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _tail(stderr) -> str:
    """The last lines the run wrote on stderr."""
    stderr.seek(0)
    text = stderr.read().decode(errors="replace").strip()
    return "\n".join(text.splitlines()[-STDERR_TAIL_LINES:])
