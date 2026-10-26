"""The hub: the latest events of one process, and "what came after this?"

Pieces that report (the jobs, later the memory's activity) publish to the
hub; screens read it. Each event gets a cursor as its id,
`<epoch>.<seq>`: `seq` counts up, and `epoch` is fresh for every hub, so a
cursor handed out before the tools server restarted is recognised as
stale.

One primitive serves every reader: `after(cursor, timeout=...)` answers
what came after `cursor`, waiting up to `timeout` when nothing has.

- An empty list means nothing new; the SSE route sends a heartbeat for it.
- A cursor the hub cannot continue from gets `[resync]`: one from another
  epoch, one that fell out of the kept window, one that is malformed. The
  resync's id is the cursor to go on from, and the reader reloads its view
  instead of showing a gap.
- `cursor=None` means only what comes next.

Why one primitive and no queue per subscriber: SSE, the poll fallback, a
bot and a test read it the same way; there is no state per reader to leak
when a connection drops; a slow reader cannot hold up a publisher; and
replay and live delivery are the same code path. Thread-safe: runs publish
from their own threads, the routes read from a thread pool.

    hub = Hub()
    seen = hub.publish("step.done", {"text": "Pulling images"}, subject="job/3f2a")
    hub.after(seen.id, timeout=15)   # what came next, or [] after 15 s

The tools server keeps one per process (`live_routes.py`). Nothing is
written to disk: a restart starts a new epoch, and every screen resyncs.
"""

from __future__ import annotations

import secrets
import threading
from collections import deque

from .envelope import RESYNC, Event

# Events kept for readers that come back. A `stack up` writes some twenty,
# so this covers a screen that was away for dozens of runs, at a few
# hundred kilobytes.
KEEP = 1000


class Hub:
    """The latest `keep` events, each with a cursor, and readers waiting for more."""

    def __init__(self, keep: int = KEEP):
        self.epoch = secrets.token_hex(4)
        self._events: deque[tuple[int, Event]] = deque(maxlen=keep)
        self._seq = 0
        self._changed = threading.Condition()

    @property
    def cursor(self) -> str:
        """The latest cursor: reading after it gets only what comes next."""
        with self._changed:
            return self._cursor(self._seq)

    def publish(self, type: str, data: dict, subject: str = "") -> Event:
        """Keep an event, wake every waiting reader, and return it with its cursor."""
        with self._changed:
            self._seq += 1
            event = Event.now(self._cursor(self._seq), type, data, subject)
            self._events.append((self._seq, event))
            self._changed.notify_all()
        return event

    def after(self, cursor: str | None, *, timeout: float) -> list[Event]:
        """What came after `cursor`, oldest first: waits up to `timeout` for
        something when nothing has; `[resync]` when it cannot continue."""
        with self._changed:
            seq = self._seq if cursor is None else self._seq_of(cursor)
            if seq is None or not self._continues(seq):
                return [self._resync()]
            self._changed.wait_for(lambda: self._seq > seq, timeout=timeout)
            if not self._continues(seq):
                return [self._resync()]
            return [event for n, event in self._events if n > seq]

    # ── Cursors ──────────────────────────────────────────────────────────

    def _cursor(self, seq: int) -> str:
        return f"{self.epoch}.{seq}"

    def _seq_of(self, cursor: str) -> int | None:
        """The seq a cursor of this hub names; None for any other."""
        epoch, dot, seq = cursor.partition(".")
        return int(seq) if dot and epoch == self.epoch and seq.isdigit() else None

    def _continues(self, seq: int) -> bool:
        """Whether every event after `seq` is still here or yet to come."""
        oldest = self._events[0][0] if self._events else self._seq + 1
        return oldest - 1 <= seq <= self._seq

    def _resync(self) -> Event:
        return Event.now(self._cursor(self._seq), RESYNC, {})
