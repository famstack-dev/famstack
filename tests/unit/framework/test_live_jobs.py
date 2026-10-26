"""Jobs: a run a screen can follow, that outlives every connection to it.

The tools server starts `stack up` as a job; the job folds each event of
the run into its snapshot and publishes it to the hub about `job/<id>`,
between a `job.started` and a `job.ended`. A screen follows the hub, or
asks for the snapshot when it comes back. These tests give a job a run
of their own (a list of events, or a generator that waits on the test)
in place of the host API, and read the job the way a screen does: from
the hub and the snapshot.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "lib"))

from stack.live.envelope import Event  # noqa: E402
from stack.live.hub import Hub  # noqa: E402
from stack.live.jobs import Jobs  # noqa: E402

PASSWORD = "donuts-4-ever"


def run_of(*events: tuple[str, dict]):
    """A run that writes these events, numbered from 1 as a run numbers them."""
    def run():
        for n, (kind, data) in enumerate(events, start=1):
            yield Event.now(n, kind, data)
    return run


UP_PHOTOS = run_of(
    ("step.done", {"text": "Rendering environment"}),
    ("step.started", {"text": "Pulling images"}),
    ("warning", {"text": "Photos is beta"}),
    ("step.done", {"text": "Pulling images", "started": 2}),
    ("step.started", {"text": "Health check: Immich"}),
    ("step.failed", {"text": "Health check: Immich", "started": 5}),
    ("result", {"ok": True, "stacklet": "photos", "name": "Photos",
                "hints": [f"Admin password: {PASSWORD}"], "env": {"DB_PASSWORD": PASSWORD}}),
)


@pytest.fixture
def hub() -> Hub:
    return Hub()


@pytest.fixture
def jobs(hub) -> Jobs:
    return Jobs(hub)


def followed(hub: Hub, cursor: str, timeout: float = 5) -> list[Event]:
    """What a screen following the hub from `cursor` sees, up to the first `job.ended`."""
    seen: list[Event] = []
    while not any(e.type == "job.ended" for e in seen):
        got = hub.after(cursor, timeout=timeout)
        assert got, "the job did not end in time"
        seen += got
        cursor = got[-1].id
    return seen


# ── A run, followed ──────────────────────────────────────────────────────

def test_a_screen_sees_the_job_start_its_steps_and_its_end(hub, jobs):
    cursor = hub.cursor
    job = jobs.start("up", "photos", UP_PHOTOS)

    seen = followed(hub, cursor)

    assert [e.type for e in seen] == [
        "job.started", "step.done", "step.started", "warning", "step.done",
        "step.started", "step.failed", "result", "job.ended"]
    assert {e.subject for e in seen} == {f"job/{job.id}"}


def test_a_step_ends_naming_the_event_a_screen_saw_start_it(hub, jobs):
    """The run numbers its events from 1; the hub's cursors replace those
    numbers, and `started` follows, so a screen can find the row to update."""
    cursor = hub.cursor
    jobs.start("up", "photos", UP_PHOTOS)

    seen = followed(hub, cursor)

    starts = {e.data["text"]: e.id for e in seen if e.type == "step.started"}
    ends = {e.data["text"]: e.data["started"] for e in seen
            if e.type in ("step.done", "step.failed") and "started" in e.data}
    assert ends == starts


def test_the_snapshot_holds_how_the_job_went(hub, jobs):
    cursor = hub.cursor
    job = jobs.start("up", "photos", UP_PHOTOS)
    followed(hub, cursor)

    snapshot = jobs.get(job.id).snapshot()

    assert {k: snapshot[k] for k in ("id", "action", "target", "state")} == {
        "id": job.id, "action": "up", "target": "photos", "state": "done"}
    assert snapshot["steps"] == [
        {"text": "Rendering environment", "state": "done"},
        {"text": "Pulling images", "state": "done"},
        {"text": "Health check: Immich", "state": "failed"},
    ]
    assert snapshot["warnings"] == ["Photos is beta"]
    assert snapshot["outcome"] == {"ok": True, "stacklet": "photos", "name": "Photos"}
    assert snapshot["started"] <= snapshot["ended"]


def test_job_ended_carries_the_snapshot(hub, jobs):
    cursor = hub.cursor
    job = jobs.start("up", "photos", UP_PHOTOS)

    ended = followed(hub, cursor)[-1]

    assert ended.data == jobs.get(job.id).snapshot()


def test_no_screen_sees_a_password_the_result_carried(hub, jobs):
    """A run's result holds the hints and the env; a screen gets only
    what `result_for_screens` lets through, in the events and the snapshot."""
    cursor = hub.cursor
    job = jobs.start("up", "photos", UP_PHOTOS)

    seen = followed(hub, cursor)

    assert [e.type for e in seen if PASSWORD in e.to_line()] == []
    assert PASSWORD not in str(jobs.get(job.id).snapshot())


# ── How a job ends ───────────────────────────────────────────────────────

@pytest.mark.parametrize("result, state", [
    ({"ok": True, "stacklet": "photos"}, "done"),
    ({"error": "Failed to start services"}, "failed"),
    ({"ok": False, "error": "Migration photos/0002 failed"}, "failed"),
    ({"cancelled": "Cancelled. Nothing changed."}, "cancelled"),
])
def test_the_result_decides_the_state(hub, jobs, result, state):
    cursor = hub.cursor
    job = jobs.start("up", "photos", run_of(("result", result)))
    followed(hub, cursor)

    snapshot = jobs.get(job.id).snapshot()

    assert (snapshot["state"], snapshot["outcome"]) == (state, result)


def test_a_run_that_raises_ends_the_job_failed_with_its_error(hub, jobs):
    def run():
        yield Event.now(1, "step.done", {"text": "Rendering environment"})
        raise ConnectionResetError("the host API went away")

    cursor = hub.cursor
    job = jobs.start("up", "photos", run)
    seen = followed(hub, cursor)

    assert [e.type for e in seen][-2:] == ["result", "job.ended"]
    assert jobs.get(job.id).snapshot()["state"] == "failed"
    assert jobs.get(job.id).snapshot()["outcome"] == {"error": "the host API went away"}


def test_a_run_that_ends_without_a_result_ends_the_job_failed(hub, jobs):
    cursor = hub.cursor
    job = jobs.start("up", "photos", run_of(("step.done", {"text": "Rendering environment"})))
    followed(hub, cursor)

    snapshot = jobs.get(job.id).snapshot()

    assert snapshot["state"] == "failed"
    assert "without a result" in snapshot["outcome"]["error"]


# ── One job per target ───────────────────────────────────────────────────

def test_a_second_start_while_the_first_runs_gets_the_running_job(hub, jobs):
    release = threading.Event()
    runs = []

    def run():
        runs.append(1)
        yield Event.now(1, "step.started", {"text": "Pulling images"})
        release.wait(5)
        yield Event.now(2, "result", {"ok": True, "stacklet": "photos"})

    cursor = hub.cursor
    first = jobs.start("up", "photos", run)
    second = jobs.start("up", "photos", run)
    running = [j.id for j in jobs.running()]
    release.set()
    followed(hub, cursor)

    assert second.id == first.id
    assert running == [first.id]
    assert runs == [1]
    assert jobs.running() == []


def test_another_target_gets_a_job_of_its_own(hub, jobs):
    cursor = hub.cursor
    photos = jobs.start("up", "photos", run_of(("result", {"ok": True})))
    followed(hub, cursor)
    docs = jobs.start("up", "docs", run_of(("result", {"ok": True})))

    assert docs.id != photos.id


# ── What is kept ─────────────────────────────────────────────────────────

def test_nothing_runs_and_nothing_is_published_until_a_job_starts(hub, jobs):
    assert jobs.running() == []
    assert hub.after(hub.cursor, timeout=0.05) == []


def test_only_the_latest_finished_jobs_are_kept():
    hub = Hub()
    jobs = Jobs(hub, keep=2)
    ids = []
    for target in ("photos", "docs", "messages"):
        cursor = hub.cursor
        ids.append(jobs.start("up", target, run_of(("result", {"ok": True}))).id)
        followed(hub, cursor)

    assert [jobs.get(i) is not None for i in ids] == [False, True, True]
    assert jobs.get("no-such-job") is None
