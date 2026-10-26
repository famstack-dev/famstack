"""The event envelope: what one line of a live event stream holds (ADR-020).

    {"id": 3, "type": "step.started", "time": "2026-10-26T09:14:02.114Z", "data": {"text": "Pulling images"}}

CloudEvents' field names and nothing else from it, no library:

- `id` orders events. A run counts from 1; the hub gives each event a
  cursor instead (`<epoch>.<seq>`).
- `type` says what happened: the constants below.
- `time` is ISO 8601 in UTC.
- `subject` says what the event is about (`job/<id>`), so a screen can
  subscribe by prefix. A run leaves it empty, and its lines then carry no
  `subject` at all.
- `data` is the payload.

Every piece of the chain uses it: the CLI writes events through the
emitter, the host API follows them, the tools server reads them with the
client and hands them to screens.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ── The types ─────────────────────────────────────────────────────────────
# Never `error`, `message` or `open`: those are a browser's own EventSource
# events, and a type of that name would be mistaken for one.

# What a run writes, `result` last and exactly once.
STEP_STARTED = "step.started"   # a slow step began
STEP_DONE = "step.done"         # a step finished; `data.started` names its start, if it had one
STEP_FAILED = "step.failed"     # a step failed; the same
WARNING = "warning"
RESULT = "result"               # what the command returns: `ok`, `error` or `cancelled`


# ── The event ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Event:
    """One event. Frozen, because an event is a fact once it happened; a
    piece that passes one on under a new id uses `dataclasses.replace`.

        Event.now(1, STEP_DONE, {"text": "Rendering environment"}).to_line()
        # '{"id": 1, "type": "step.done", "time": "2026-...Z", "data": {"text": ...}}\\n'
    """

    id: int | str
    type: str
    time: str
    data: dict = field(default_factory=dict)
    subject: str = ""

    @classmethod
    def now(cls, id: int | str, type: str, data: dict, subject: str = "") -> Event:
        """An event that happens now."""
        time = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        return cls(id, type, time.replace("+00:00", "Z"), data, subject)

    def to_dict(self) -> dict:
        """The envelope as JSON has it; no `subject` when it is empty."""
        about = {"subject": self.subject} if self.subject else {}
        return {"id": self.id, "type": self.type, "time": self.time, **about, "data": self.data}

    def to_line(self) -> str:
        """One line of JSON, ending in a newline."""
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str) + "\n"

    @classmethod
    def from_line(cls, line: str | bytes) -> Event | None:
        """The event a line holds, or None for a line that is not one."""
        try:
            raw = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(raw, dict) or "id" not in raw or not isinstance(raw.get("type"), str):
            return None
        data = raw.get("data")
        return cls(raw["id"], raw["type"], str(raw.get("time", "")),
                   data if isinstance(data, dict) else {}, str(raw.get("subject") or ""))
