"""Following a job on the dashboard: the event stream, its fallback, the jobs.

A container starts a job on core's tools server (`POST /api/jobs` on the
internal listener); a browser on the home network follows it over
Server-Sent Events (`/dashboard/api/events` on the published port), or
polls when it cannot hold a stream (`/dashboard/api/poll`), and asks for a
job's snapshot when it comes back (`/dashboard/api/jobs/<id>`). ADR-020
has the decision.

Driven through the real thing: the tools server's FastAPI app with its
access rule, one `TestClient` per listener (the Browser DSL), the real
hub and jobs. The host API is the one stand-in, at its edge:
`stack.live.client.stream` is replaced by a run that writes what a
`stack up` writes and waits for the test between steps. One test leaves
the client as it is and points it at a port where nothing listens.

The waits the routes use are shortened for the tests (`HEARTBEAT_SECS`,
`POLL_WAIT_SECS`); everything else runs as in the container.
"""

from __future__ import annotations

import socket
import sys
import threading
import time

import pytest

from .conftest import LIB, STACKLETS
from .dsl import Browser

TOOLS = STACKLETS / "core" / "tools-server"
for _path in (LIB, TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from access import INTERNAL_PORT, PUBLIC_PORT  # noqa: E402
from stack.live import client  # noqa: E402
from stack.live.envelope import Event  # noqa: E402
from stack.live.hub import Hub  # noqa: E402
from stack.live.jobs import Jobs  # noqa: E402

PASSWORD = "donuts-4-ever"


class HostAPI:
    """A stand-in for the host API at its edge: `client.stream` as a
    `stack up` answers it, waiting for the test after its first step."""

    def __init__(self):
        self.calls: list[dict] = []
        self._released = threading.Event()

    def release(self) -> None:
        self._released.set()

    def stream(self, request: dict, *, silence_secs: float):
        self.calls.append(request)
        yield Event.now(1, "step.done", {"text": "Rendering environment"})
        yield Event.now(2, "step.started", {"text": "Pulling images"})
        assert self._released.wait(5), "the test never released the run"
        yield Event.now(3, "step.done", {"text": "Pulling images", "started": 2})
        yield Event.now(4, "result", {"ok": True, "stacklet": request["stacklet"],
                                      "hints": [f"Admin password: {PASSWORD}"]})


@pytest.fixture
def live(monkeypatch):
    """The tools server with a fresh hub and jobs, as after a start."""
    import live_routes

    hub = Hub()
    monkeypatch.setattr(live_routes, "hub", hub)
    monkeypatch.setattr(live_routes, "jobs", Jobs(hub))
    monkeypatch.setattr(live_routes, "HEARTBEAT_SECS", 0.1)
    monkeypatch.setattr(live_routes, "POLL_WAIT_SECS", 0.2)
    return live_routes


@pytest.fixture
def host_api(monkeypatch, live) -> HostAPI:
    api = HostAPI()
    monkeypatch.setattr(client, "stream", api.stream)
    return api


@pytest.fixture
def app(live):
    import server

    return server.app


@pytest.fixture
def phone(app) -> Browser:
    """A phone on the home network, opening the Mac's published port."""
    return Browser(app, listener=PUBLIC_PORT, host="192.168.1.20:42000")


@pytest.fixture
def container(app) -> Browser:
    """One of the stack's containers, on the internal listener."""
    return Browser(app, listener=INTERNAL_PORT, host="stack-core-tools:8000")


def start_up(container: Browser, target: str = "photos") -> dict:
    status, job = container.posts("/api/jobs", {"action": "up", "target": target})
    assert status == 202, job
    return job


def settled(browser: Browser, job_id: str, within: float = 5) -> dict:
    """The job's snapshot once it is no longer running."""
    deadline = time.monotonic() + within
    while True:
        status, job = browser.reads(f"/dashboard/api/jobs/{job_id}")
        assert status == 200, job
        if job["state"] != "running":
            return job
        assert time.monotonic() < deadline, f"still running after {within} s"
        time.sleep(0.02)


def a_closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def types(frames) -> list[str]:
    return [f.event["type"] for f in frames]


# ── Following a job ──────────────────────────────────────────────────────

def test_a_phone_follows_a_job_a_container_started(phone, container, host_api):
    """Given a job started on the internal listener, when a phone follows
    the stream on the published port, then it gets the job's start, the
    run's steps and its end, each about the job and with a cursor id."""
    with phone.follows("/dashboard/api/events") as stream:
        opening = stream.next_frame()
        job = start_up(container)
        host_api.release()
        frames = stream.events_until("job.ended")

    assert opening.retry == 3000
    assert types(frames) == ["job.started", "step.done", "step.started", "step.done",
                             "result", "job.ended"]
    assert {f.event["subject"] for f in frames} == {f"job/{job['id']}"}
    assert [f.id for f in frames] == [f.event["id"] for f in frames]
    assert frames[-1].event["data"]["state"] == "done"


def test_no_frame_carries_a_password_the_result_held(phone, container, host_api):
    with phone.follows("/dashboard/api/events") as stream:
        stream.next_frame()
        job = start_up(container)
        host_api.release()
        frames = stream.events_until("job.ended")

    assert [f.event["type"] for f in frames if PASSWORD in str(f.event)] == []
    assert PASSWORD not in str(settled(phone, job["id"]))


def test_a_stream_says_it_is_not_cached_or_buffered(phone, live):
    with phone.follows("/dashboard/api/events") as stream:
        assert stream.status == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert stream.headers["cache-control"] == "no-cache"
        assert stream.headers["x-accel-buffering"] == "no"


# ── Coming back ──────────────────────────────────────────────────────────

def test_a_browser_that_reconnects_gets_exactly_what_it_missed(phone, container, host_api):
    """EventSource sends the id of the last event it saw; the stream goes
    on from there, with nothing lost and nothing twice."""
    with phone.follows("/dashboard/api/events") as stream:
        stream.next_frame()
        job = start_up(container)
        before = stream.events_until("step.started")
    host_api.release()
    settled(phone, job["id"])
    with phone.follows("/dashboard/api/events", last_event_id=before[-1].id) as stream:
        stream.next_frame()
        after = stream.events_until("job.ended")

    assert types(before) + types(after) == ["job.started", "step.done", "step.started",
                                            "step.done", "result", "job.ended"]


def test_a_cursor_from_before_a_restart_gets_a_resync(phone, live):
    with phone.follows("/dashboard/api/events", last_event_id="0badcafe.12") as stream:
        stream.next_frame()
        resync = stream.events_until("resync")

    assert types(resync) == ["resync"]
    assert resync[0].id == live.hub.cursor


def test_a_cursor_too_old_for_what_is_kept_gets_a_resync(phone, container, host_api, live, monkeypatch):
    hub = Hub(keep=2)
    monkeypatch.setattr(live, "hub", hub)
    monkeypatch.setattr(live, "jobs", Jobs(hub))
    first = hub.cursor
    job = start_up(container)
    host_api.release()
    settled(container, job["id"])

    with phone.follows("/dashboard/api/events", last_event_id=first) as stream:
        stream.next_frame()
        assert types(stream.events_until("resync")) == ["resync"]


def test_a_job_runs_on_when_its_browser_goes_away(phone, container, host_api):
    """The state lives in the job, not in the connection: the snapshot
    says how it ended to the browser that comes back."""
    with phone.follows("/dashboard/api/events") as stream:
        stream.next_frame()
        job = start_up(container)
        stream.events_until("job.started")
    host_api.release()

    snapshot = settled(phone, job["id"])

    assert snapshot["state"] == "done"
    assert snapshot["outcome"] == {"ok": True, "stacklet": "photos"}
    assert [s["state"] for s in snapshot["steps"]] == ["done", "done"]


# ── Starting a job ───────────────────────────────────────────────────────

def test_a_second_start_for_the_same_target_gets_the_running_job(container, host_api):
    first = start_up(container)
    second = start_up(container)
    host_api.release()
    settled(container, first["id"])

    assert second["id"] == first["id"]
    assert len(host_api.calls) == 1


def test_the_network_cannot_start_a_job(phone, host_api):
    """Only the internal listener serves /api/jobs: on the published port it
    is a 404 like any path outside /go and /dashboard."""
    status, _ = phone.posts("/api/jobs", {"action": "up", "target": "photos"})

    assert status == 404
    assert host_api.calls == []


def test_an_action_no_one_named_is_refused(container, host_api):
    status, answer = container.posts("/api/jobs", {"action": "destroy", "target": "photos"})

    assert status == 400
    assert answer["actions"] == ["up"]
    assert host_api.calls == []


def test_with_the_host_api_down_the_job_ends_failed(container, live, monkeypatch):
    """The real client, pointed where nothing listens."""
    monkeypatch.setenv("STACK_API_HOST", "127.0.0.1")
    monkeypatch.setenv("STACK_API_PORT", str(a_closed_port()))

    snapshot = settled(container, start_up(container)["id"])

    assert snapshot["state"] == "failed"
    assert snapshot["outcome"] == {"error": "the host API is not running"}


def test_an_unknown_job_is_not_found(phone, live):
    status, _ = phone.reads("/dashboard/api/jobs/no-such-job")

    assert status == 404


# ── The fallback, and the resting state ──────────────────────────────────

def test_polling_gets_what_came_after_the_cursor_and_the_next_cursor(phone, container, host_api, live):
    start = live.hub.cursor
    job = start_up(container)
    host_api.release()
    settled(container, job["id"])

    status, answer = phone.reads(f"/dashboard/api/poll?after={start}")
    _, nothing_new = phone.reads(f"/dashboard/api/poll?after={answer['cursor']}")

    assert status == 200
    assert [e["type"] for e in answer["events"]][0] == "job.started"
    assert answer["cursor"] == answer["events"][-1]["id"] == live.hub.cursor
    assert nothing_new == {"events": [], "cursor": answer["cursor"]}


def test_with_no_job_running_an_open_stream_hears_only_heartbeats(phone, live):
    with phone.follows("/dashboard/api/events") as stream:
        stream.next_frame()
        frames = stream.frames_for(0.4)

    assert frames
    assert [f for f in frames if f.event is not None or f.comment != "heartbeat"] == []


# ── The page's side ──────────────────────────────────────────────────────

def test_the_dashboard_loads_its_event_stream_script(phone, live, monkeypatch):
    """`live.js` holds the page's one stream; the page loads it, and it is
    served as the module it is. What it does in a browser is checked in
    one, not here: there is no JavaScript runner in this suite."""
    monkeypatch.setenv("MEMORY_CARDS_URL", f"http://127.0.0.1:{a_closed_port()}")
    page = phone.opens("/dashboard")
    script = phone.opens("/dashboard/static/live.js")

    assert "/dashboard/static/live.js" in page.scripts()
    assert script.status == 200
    assert script.kind.startswith("text/javascript")
