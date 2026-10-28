"""The live routes: jobs a screen follows, over Server-Sent Events or polling.

ADR-020 has the decision; `stack.live` has the pieces. These routes are
thin adapters over the one `Hub` and the one `Jobs` this process keeps
(the tools server is one uvicorn process, `server.py`):

    GET  /dashboard/api/events      the stream (SSE), from `Last-Event-ID` or `?after=`
    GET  /dashboard/api/poll        the fallback: one `after(cursor)` as JSON
    GET  /dashboard/api/jobs/{id}   a job's snapshot, or 404
    POST /dashboard/api/jobs        {"action": "up", "target": "<id>"}: 202 and the snapshot

Each event goes out with its cursor as the SSE `id` and the JSON envelope
as its data, with no SSE `event:` name: the browser hands every event to
one handler (`dashboard/live.js`), which reads the type from the envelope.

A stream waits in a worker thread of the server's pool between events, so
each open stream holds one. A household's few screens are far from the
pool's forty.
"""

from __future__ import annotations

import functools
import json
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

from access import DASHBOARD_PREFIX
from stack.live import client
from stack.live.hub import Hub
from stack.live.jobs import Jobs

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


def _up(target: str):
    """`stack up <target>`, followed through the host API."""
    return client.stream({"cmd": "up", "stacklet": target}, silence_secs=STREAM_SILENCE_SECS)


# What a job may do. The one place that widens it: an action added here is
# a run the host API streams (`STREAMED_COMMANDS` in famstack-api.py).
ACTIONS = {"up": _up}


# ── Following ─────────────────────────────────────────────────────────────

@router.get(f"{DASHBOARD_PREFIX}/api/events", response_class=EventSourceResponse,
            include_in_schema=False)
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


@router.get(f"{DASHBOARD_PREFIX}/api/poll", include_in_schema=False)
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


# Under /dashboard, so the dashboard's Add button reaches it: it is open on
# the home network for now, like everything the dashboard shows (decided
# 2026-10-26, the feature first). The backlog entry "Basic security for
# the tools server" closes it again, to this Mac or to a signed-in admin.
@router.post(f"{DASHBOARD_PREFIX}/api/jobs", status_code=202, include_in_schema=False)
def start_job(request: JobRequest):
    """Start `action` on `target`, or join the job already running it."""
    run = ACTIONS.get(request.action)
    if run is None:
        return JSONResponse({"error": f"no action {request.action!r}", "actions": sorted(ACTIONS)},
                            status_code=400)
    if not TARGET.fullmatch(request.target):
        return JSONResponse({"error": f"{request.target!r} is not a stacklet id"}, status_code=400)
    started = jobs.start(request.action, request.target, functools.partial(run, request.target))
    return JSONResponse(started.snapshot(), status_code=202)
