"""The live routes: jobs a screen follows, over Server-Sent Events or polling.

ADR-020 has the decision; `stack.live` has the pieces. These routes are
thin adapters over the one `Hub` and the one `Jobs` this process keeps
(the tools server is one uvicorn process, `server.py`):

    GET  /dashboard/api/events      the stream (SSE), from `Last-Event-ID` or `?after=`
    GET  /dashboard/api/poll        the fallback: one `after(cursor)` as JSON
    GET  /dashboard/api/jobs/{id}   a job's snapshot, or 404
    POST /dashboard/api/jobs        {"action": "up", "target": "<id>"}: 202 and the snapshot

Besides the jobs a page starts, the routes' jobs follow every other run
on the Mac: `watching()` holds the host API's feed of runs for the life
of the process (`stack.live.watch`, started by the app's lifespan in
`server.py`), so a start from the terminal shows on its tile too.

Each event goes out with its cursor as the SSE `id` and the JSON envelope
as its data, with no SSE `event:` name: the browser hands every event to
one handler (`dashboard/live.js`), which reads the type from the envelope.

A stream waits in a worker thread of the server's pool between events, so
each open stream holds one, and a poll holds one while it waits. The
server's pool has forty, and the `/go` links and core's health check need
them too, so at most `STREAMS_CAP` streams and polls are open at a time;
one more is answered 429 and the browser tries again (`RETRY_MS`).
A household's few screens, one stream each, are far from the cap:

    GET /dashboard/api/events     200, a stream          (the 1st to the 20th open)
    GET /dashboard/api/events     429, Retry-After: 3    (the 21st)
    GET /go/<card id>             answered as always

The cap counts every device's streams together. It cannot count per
device: Docker's port forwarding gives every request the same address.
"""

from __future__ import annotations

import contextlib
import functools
import json
import re
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

from access import DASHBOARD_PREFIX, mac_only
from stack.live import client
from stack.live.hub import Hub
from stack.live.jobs import Jobs
from stack.live.watch import watch

router = APIRouter()

# One per process: every route and every job of this server shares them.
hub = Hub()
jobs = Jobs(hub)

# How long a browser waits before it reconnects a broken stream.
RETRY_MS = 3000
# An idle stream sends a comment this often, so the browser, Caddy and any
# proxy between see it is alive.
HEARTBEAT_SECS = 15
# How long one poll waits for something new: under the 30 s after which
# many proxies and clients give up on a request.
POLL_WAIT_SECS = 25
# The host API ends a run that says nothing for 1260 s and sends its result
# (`STREAM_SILENCE_SECS` in famstack-api.py). A minute more lets that
# result arrive before this side gives up on the API itself.
STREAM_SILENCE_SECS = 1260 + 60
# What a target may be: a stacklet id, or `all`.
TARGET = re.compile(r"[a-z0-9_]+")


# How many streams and polls may be open at a time: half the server's pool
# of forty worker threads (anyio's default), so the other half always
# serves the `/go` links, core's health check and the pages.
STREAMS_CAP = 20


# How long the watcher waits before it opens the host's feed of runs again.
WATCH_RETRY_SECS = 5


def _up(target: str):
    """`stack up <target>`, started by the host API and followed from its
    run log, with the dashboard named as where it was started."""
    return client.stream({"cmd": "up", "stacklet": target, "surface": "dashboard"},
                         silence_secs=STREAM_SILENCE_SECS)


# What a job may do. The one place that widens it: an action added here is
# a run the host API streams (`STREAMED_COMMANDS` in famstack-api.py).
ACTIONS = {"up": _up}


@contextlib.contextmanager
def watching():
    """Follow every run on the Mac as a job of this process, for the
    `with` block: the life of the server."""
    stop = threading.Event()
    threading.Thread(target=watch, args=(jobs, stop), kwargs={"retry_secs": WATCH_RETRY_SECS},
                     daemon=True, name="watch-runs").start()
    try:
        yield
    finally:
        stop.set()


# ── Following ─────────────────────────────────────────────────────────────

def a_stream_slot():
    """A route dependency: hold one of the `STREAMS_CAP` places for as long
    as the answer is sent, the whole stream for an event stream; answer 429
    when none is free, before the route waits for anything. FastAPI runs
    the code after `yield` once the answer has been sent or the browser
    went away."""
    if not _open_streams.take():
        raise HTTPException(429, "Too many live streams are open. Try again in a moment.",
                            headers={"Retry-After": str(RETRY_MS // 1000)})
    try:
        yield
    finally:
        _open_streams.give_back()


@router.get(f"{DASHBOARD_PREFIX}/api/events", response_class=EventSourceResponse,
            include_in_schema=False, dependencies=[Depends(a_stream_slot)])
def events(request: Request, after: str | None = None):
    """The stream: what came after the browser's cursor, then each event as
    it is published, a heartbeat when there is none.

    The cursor is fixed before the first frame goes out, so a reader that
    has the first frame misses nothing published after it.
    """
    cursor = request.headers.get("last-event-id") or after or hub.cursor
    yield ServerSentEvent(retry=RETRY_MS, comment="live")
    while True:
        published = hub.after(cursor, timeout=HEARTBEAT_SECS)
        if not published:
            yield ServerSentEvent(comment="heartbeat")
        for event in published:
            cursor = str(event.id)
            yield ServerSentEvent(id=str(event.id), raw_data=json.dumps(event.to_dict()))


@router.get(f"{DASHBOARD_PREFIX}/api/poll", include_in_schema=False,
            dependencies=[Depends(a_stream_slot)])
def poll(after: str | None = None):
    """The fallback for a browser that cannot hold a stream (RFC-001:
    "SSE for real-time, polling as fallback"): one wait for what came
    after `after`, and the cursor to ask with next."""
    cursor = after or hub.cursor
    published = hub.after(cursor, timeout=POLL_WAIT_SECS)
    return {"events": [event.to_dict() for event in published],
            "cursor": published[-1].id if published else cursor}


@router.get(f"{DASHBOARD_PREFIX}/api/jobs/{{job_id}}", include_in_schema=False)
def job(job_id: str):
    found = jobs.get(job_id)
    if found is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return found.snapshot()


# ── Starting ──────────────────────────────────────────────────────────────

class JobRequest(BaseModel):
    action: str
    target: str


# A change: Mac-only (`mac_only`), so the home address follows a start live
# and never makes one. The stacker bot starts stacklets through the host
# API, not through this route.
@router.post(f"{DASHBOARD_PREFIX}/api/jobs", status_code=202, include_in_schema=False,
             dependencies=[Depends(mac_only)])
def start_job(request: JobRequest):
    """Start `action` on `target`, or join the job already running it;
    403 through the home address."""
    run = ACTIONS.get(request.action)
    if run is None:
        return JSONResponse({"error": f"no action {request.action!r}", "actions": sorted(ACTIONS)},
                            status_code=400)
    if not TARGET.fullmatch(request.target):
        return JSONResponse({"error": f"{request.target!r} is not a stacklet id"}, status_code=400)
    started = jobs.start(request.action, request.target, functools.partial(run, request.target))
    return JSONResponse(started.snapshot(), status_code=202)


# ── Counting open streams ─────────────────────────────────────────────────

class _OpenStreams:
    """How many streams and polls are open now, counted across threads."""

    def __init__(self):
        self._lock = threading.Lock()
        self.count = 0

    def take(self) -> bool:
        """One more open stream, or False when `STREAMS_CAP` are open."""
        with self._lock:
            if self.count >= STREAMS_CAP:
                return False
            self.count += 1
            return True

    def give_back(self) -> None:
        with self._lock:
            self.count -= 1


_open_streams = _OpenStreams()
