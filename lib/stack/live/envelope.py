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

What a screen may see. A step's text and a warning are words for people
and never carry a secret's value: a hook says "Creating the admin
account", never the password it chose (`stacklets/docs/auth.py` keeps to
that). A run's result goes through `result_for_screens`, the one list of
what it may show.
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

# What the jobs write around a run (jobs.py); their data is the job's snapshot.
JOB_STARTED = "job.started"
JOB_ENDED = "job.ended"

# The hub's answer to a cursor it cannot continue from (hub.py).
RESYNC = "resync"


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
        return cls(id, type, utc_now(), data, subject)

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


def utc_now() -> str:
    """Now, as an event's `time` has it: ISO 8601 in UTC, to the millisecond."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ── What a result may show ────────────────────────────────────────────────
# A list of what may pass, not of what may not: `stack up`'s result also
# holds the rendered env and the manifest's hints, and both can carry a
# password (`ai`'s hint names the oMLX admin password). A field added to
# the result stays off every screen until it is named here. A caller that
# needs the rest asks the host API without `stream` and gets it all.
# `healthy` is whether every health check passed: a run can be `ok` and
# not healthy yet (ADR-020), and a screen says so.
FOR_SCREENS = ("ok", "error", "cancelled", "stacklet", "name", "stage",
               "first_run", "warnings", "migrations", "healthy")


def result_for_screens(result: dict) -> dict:
    """The part of a run's result a screen may show."""
    return {key: result[key] for key in FOR_SCREENS if key in result}
