"""Numbers a run's events and writes each one the moment it happens.

A run, such as one `stack up`, reports to screens as one line of JSON per
event (envelope.py). The emitter gives each event the next id, counting
from 1 within the run, and writes and flushes it at once, so whoever
reads the stream sees it while the run goes on. `output.EventOutput`, the
adapter `stack up --events` reports through, is its user; anything else
that reports a run the same way can use it on its own.

    emitter = Emitter(sys.stdout)
    emitter.emit(STEP_DONE, {"text": "Rendering environment"}).id   # 1
"""

from __future__ import annotations

import itertools
import threading

from .envelope import Event


class Emitter:
    """Writes a run's events to `stream`, numbered from 1, one line each.

    Safe to call from several threads: a hook may report from one of its
    own, and the ids then still count up in the order the lines appear.
    """

    def __init__(self, stream):
        self.stream = stream
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def emit(self, type: str, data: dict) -> Event:
        """Write the run's next event now; returns it, id included."""
        with self._lock:
            event = Event.now(next(self._ids), type, data)
            self.stream.write(event.to_line())
            self.stream.flush()
        return event
