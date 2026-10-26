"""Jobs: runs a screen can follow, which outlive every connection to them.

A job is one run of an action on a target (`up` on `photos`), started by
whoever asks and followed by any number of screens. `Jobs.start` runs it
in a thread of its own: each event of the run is folded into the job and
published to the hub about `job/<id>`, `job.started` first and `job.ended`
last, both carrying the job's snapshot. The run is injected, so the same
jobs run `stack up` through the host API in the tools server and a list of
events in a test:

    jobs = Jobs(hub)
    job = jobs.start("up", "photos", lambda: client.stream({"cmd": "up", "stacklet": "photos"},
                                                            silence_secs=1320))
    jobs.get(job.id).snapshot()   # {"id": ..., "state": "running", "steps": [...], ...}

- **One running job per action and target.** A second start while the
  first runs gets the running one; nothing runs twice.
- **The state lives in the job, not in a connection** (ADR-020). A screen
  that went away asks for the snapshot when it comes back. A run that
  raises ends its job failed, with the error as its outcome.
- **A snapshot is what a screen may see:** the steps (text and state), the
  warnings, and the outcome through `result_for_screens`. The run's result
  is filtered when it arrives, so the job never holds the hints or the env.
- **Bounded:** the running jobs and the latest `keep` finished ones, in
  memory. A restart forgets them, and screens resync.

The tools server keeps one per process (`live_routes.py`).
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable, Iterable

from .envelope import (JOB_ENDED, JOB_STARTED, RESULT, STEP_DONE, STEP_FAILED, STEP_STARTED,
                       WARNING, Event, result_for_screens, utc_now)
from .hub import Hub

# A job's states. `running` until its run's result says how it went.
RUNNING, DONE, FAILED, CANCELLED = "running", "done", "failed", "cancelled"

# Finished jobs kept for a screen that comes back to see how they went.
KEEP_FINISHED = 20

Run = Callable[[], Iterable[Event]]


class Job:
    """One run of `action` on `target`, folded into what a screen may see.

        job.snapshot()["steps"]   # [{"text": "Pulling images", "state": "done"}, ...]
    """

    def __init__(self, action: str, target: str):
        self.id = secrets.token_hex(6)
        self.action = action
        self.target = target
        self.subject = f"job/{self.id}"
        self.state = RUNNING
        self.started = utc_now()
        self.ended: str | None = None
        self.outcome: dict | None = None
        self._steps: list[dict] = []
        self._open: dict = {}       # a run's step.started id -> its step
        self._warnings: list[str] = []
        self._lock = threading.Lock()

    def snapshot(self) -> dict:
        """The job as a screen may see it."""
        with self._lock:
            return {"id": self.id, "action": self.action, "target": self.target,
                    "state": self.state, "started": self.started, "ended": self.ended,
                    "steps": [dict(step) for step in self._steps],
                    "warnings": list(self._warnings),
                    "outcome": dict(self.outcome) if self.outcome is not None else None}

    def fold(self, event: Event) -> None:
        """Take one event of the run into the job."""
        with self._lock:
            text = event.data.get("text", "")
            if event.type == STEP_STARTED:
                self._open[event.id] = {"text": text, "state": RUNNING}
                self._steps.append(self._open[event.id])
            elif event.type in (STEP_DONE, STEP_FAILED):
                state = DONE if event.type == STEP_DONE else FAILED
                step = self._open.pop(event.data.get("started"), None)
                if step is None:
                    self._steps.append({"text": text, "state": state})
                else:
                    step["state"] = state
            elif event.type == WARNING:
                self._warnings.append(text)
            elif event.type == RESULT:
                self.outcome = result_for_screens(event.data)

    def end(self) -> None:
        """Settle the state from the outcome, and the time it ended."""
        with self._lock:
            self.state = _state_of(self.outcome or {})
            self.ended = utc_now()


class Jobs:
    """The jobs of one process, publishing to `hub`."""

    def __init__(self, hub: Hub, keep: int = KEEP_FINISHED):
        self.hub = hub
        self.keep = keep
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, action: str, target: str, run: Run) -> Job:
        """Start `run` as the job for `action` on `target`, or return the
        one already running for them."""
        with self._lock:
            for job in self._jobs.values():
                if (job.action, job.target, job.state) == (action, target, RUNNING):
                    return job
            job = Job(action, target)
            self._jobs[job.id] = job
        self.hub.publish(JOB_STARTED, job.snapshot(), subject=job.subject)
        threading.Thread(target=self._run, args=(job, run), daemon=True,
                         name=f"job-{job.id}").start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def running(self) -> list[Job]:
        with self._lock:
            return [job for job in self._jobs.values() if job.state == RUNNING]

    def recent(self) -> list[Job]:
        """The running jobs and the kept finished ones, oldest first: what a
        screen shows next to each target, a run going on or how it ended."""
        with self._lock:
            return list(self._jobs.values())

    # ── Running one ──────────────────────────────────────────────────────

    def _run(self, job: Job, run: Run) -> None:
        """Pass the run's events on until its result, then end the job.

        Every failure of the run ends the job, never the thread silently:
        an exception becomes the job's result, and so does a run that
        stops without one.
        """
        cursors: dict = {}
        try:
            for event in run():
                self._pass_on(job, event, cursors)
                if job.outcome is not None:
                    break
        except Exception as e:
            self._pass_on(job, Event.now(0, RESULT, {"error": str(e) or type(e).__name__}), cursors)
        if job.outcome is None:
            error = f"{job.action} {job.target} ended without a result"
            self._pass_on(job, Event.now(0, RESULT, {"error": error}), cursors)
        job.end()
        self.hub.publish(JOB_ENDED, job.snapshot(), subject=job.subject)
        self._forget_old()

    def _pass_on(self, job: Job, event: Event, cursors: dict) -> None:
        """Fold an event into the job and publish it about the job.

        The hub gives it a cursor in place of the run's number, so a step's
        `started` is rewritten to the cursor its start was published under:
        a screen finds the row to update by the id it saw.
        """
        job.fold(event)
        data = event.data
        if event.type == RESULT:
            data = result_for_screens(data)
        elif "started" in data:
            data = {**data, "started": cursors.get(data["started"], data["started"])}
        published = self.hub.publish(event.type, data, subject=job.subject)
        if event.type == STEP_STARTED:
            cursors[event.id] = published.id

    def _forget_old(self) -> None:
        with self._lock:
            finished = sorted((job for job in self._jobs.values() if job.state != RUNNING),
                              key=lambda job: job.ended or "")
            for job in finished[:-self.keep] if self.keep else finished:
                del self._jobs[job.id]


def _state_of(outcome: dict) -> str:
    """A job's end state from its outcome. `healthy` plays no part: a run
    that came up but does not answer yet is done, as `stack up` exits 0
    for it (ADR-020); the outcome keeps `healthy: false` for the screen."""
    if "cancelled" in outcome:
        return CANCELLED
    if outcome.get("ok") and "error" not in outcome:
        return DONE
    return FAILED
