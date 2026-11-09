"""Every run on the Mac as a job, wherever it was started.

A run of a stacklet can start in a terminal, in the Server room, in the
menu bar app or on the dashboard. Each writes its events to the stacklet's
run log on the Mac, and the host API reports each run as it starts (its
`runs` feed) and follows one from its log (`follow`, both in
`stack.live.follow`). `watch` holds the feed open and starts a job for
each run it reports, so a screen shows the steps of a start it did not
make, as it shows its own:

    stop = threading.Event()
    threading.Thread(target=watch, args=(jobs, stop), daemon=True).start()
    ...
    stop.set()

- **One job per run.** A job knows its run from the run's first event
  (`Job.run`). A run that already has a job, the page's own Start among
  them, gets no second one; neither does a second run of a stacklet while
  a job of the same action on it still runs (`Jobs.start`).
- **A restart loses nothing.** The feed reports the runs going on when it
  connects, and a job follows its run from the start of the log. A tools
  server that restarts in the middle of a run (core's refresh at the end
  of `stack up`) picks it up again with every step so far, once each.
- **At rest it only listens.** Between runs the feed sends a heartbeat
  every few seconds; nothing here asks the host for anything.

The tools server runs it for the life of its process (`live_routes.py`).
"""

from __future__ import annotations

import functools
import threading

from . import client
from .envelope import RUN
from .jobs import Job, Jobs

# The host API's `runs` feed says it is alive every 30 s
# (`RUNS_HEARTBEAT_SECS` in famstack-api.py). Three missed heartbeats and
# the feed counts as gone, and is opened again.
WATCH_SILENCE_SECS = 3 * 30
# How long to wait before opening the feed again after it ended, so an API
# that is down is not asked in a tight loop.
RETRY_SECS = 5
# A followed run that says nothing for this long is given up on. The host
# API ends a run it started after 1260 s of silence and sends its result
# (`STREAM_SILENCE_SECS` there); a minute more lets that arrive first.
FOLLOW_SILENCE_SECS = 1260 + 60


def watch(jobs: Jobs, stop: threading.Event, *, retry_secs: float = RETRY_SECS) -> None:
    """Start a job in `jobs` for each run the host API reports, until
    `stop` is set. Opens the feed again whenever it ends: the API
    restarted, refused, or fell silent."""
    while not stop.is_set():
        for event in client.stream({"cmd": "runs"}, silence_secs=WATCH_SILENCE_SECS):
            if stop.is_set():
                return
            if event.type == RUN:
                follow_run(jobs, event.data)
        stop.wait(retry_secs)


def follow_run(jobs: Jobs, run: dict) -> Job | None:
    """The job that follows `run` (a `run` event's data): the one it has,
    or a new one that follows it from its log. None for a run that names
    no stacklet."""
    stacklet = run.get("stacklet")
    if not isinstance(stacklet, str) or not stacklet:
        return None
    if (job := jobs.following(run)) is not None:
        return job
    return jobs.start(run.get("action") or "up", stacklet, functools.partial(_follow, stacklet))


def _follow(stacklet: str):
    """The stacklet's current run, from its log, through the host API."""
    return client.stream({"cmd": "follow", "stacklet": stacklet}, silence_secs=FOLLOW_SILENCE_SECS)
