"""The "Your stack" widget: what runs, and what the family can add.

The dashboard's second widget. It reads the stack's state from the host
API (`stack list --json`), lists what runs with its state and a link,
and offers what may be added: stable and beta stacklets, never a
deprecated one, never one that is always on. A stacklet whose first `up`
asks questions (`asks_on_first_up`) is offered with its terminal command,
not with an Add button.

Driven through the tools server's FastAPI app as a phone on the home
network opens it (the Browser DSL). The host API is the stand-in, at its
edge: `stack.live.client.call` answers `list` with what the real
`Stack.list()` reports over the repo's own stacklets, with Docker stood
in for (which containers run, which health checks pass). The page is
read as served, without JavaScript.
"""

from __future__ import annotations

import json
import sys
import threading
import time

import pytest

from .conftest import LIB, REPO, STACKLETS
from .dsl import Browser

TOOLS = STACKLETS / "core" / "tools-server"
for _path in (LIB, TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from access import PUBLIC_PORT  # noqa: E402
from stack.live import client  # noqa: E402
from stack.live.envelope import Event  # noqa: E402
from stack.live.hub import Hub  # noqa: E402
from stack.live.jobs import Jobs  # noqa: E402

# Messages and documents run, the memory runs but its check fails; the
# rest were never set up.
RUNNING = ("core", "messages", "docs", "memory")
NOT_ANSWERING = ("memory",)


def listing(tmp_path, monkeypatch, running=RUNNING, not_answering=NOT_ANSWERING) -> dict:
    """What `stack list --json` answers for an instance where `running`
    were set up and run, as the host API would hand it over."""
    import stack.docker
    from stack import Stack

    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\nhost = "server.lan"\n'
        f'extension_dirs = ["{tmp_path / "extensions"}"]\n\n[messages]\nserver_name = "simpson"\n')
    (tmp_path / ".stack").mkdir(exist_ok=True)
    for sid in running:
        (tmp_path / ".stack" / f"{sid}.setup-done").touch()
    stck = Stack(REPO, tmp_path / "data", instance_dir=tmp_path)
    failing_checks = {s["manifest"].get("health", {}).get("url")
                      for s in stck.discover() if s["id"] in not_answering}
    with monkeypatch.context() as docker:
        docker.setattr(stack.docker, "project_states", lambda: {sid: "running" for sid in running})
        docker.setattr(stack.docker, "check_health", lambda url, headers=None: url not in failing_checks)
        answer = stck.list()
    return json.loads(json.dumps(answer, default=str))


@pytest.fixture
def phone(tmp_path, monkeypatch):
    """A phone on the home network, with a host API that answers `list`."""
    import server

    answer = listing(tmp_path, monkeypatch)
    monkeypatch.setattr(client, "call", lambda request, timeout: answer
                        if request == {"cmd": "list"} else {"error": "not stood in"})
    return Browser(server.app, listener=PUBLIC_PORT, host="192.168.1.20:42000")


def rows(page) -> list[tuple[str, str]]:
    """(name, state) of every stacklet the widget lists as set up."""
    return list(zip(page.texts("stack-name"), page.texts("stack-state")))


# ── What runs ────────────────────────────────────────────────────────────

def test_the_widget_lists_what_is_set_up_with_its_state(phone):
    page = phone.opens("/dashboard")

    assert rows(page) == [("Core", "running"), ("Documents", "running"),
                          ("Family memory", "started, not answering yet"), ("Chat", "running")]


def test_a_running_stacklet_links_to_where_it_is_opened(phone):
    page = phone.opens("/dashboard")

    assert ("Open", "http://server.lan:42020") in page.links("stack-row")


def test_the_glance_says_how_many_run(phone):
    assert phone.opens("/dashboard").texts("stack-glance") == ["4 running"]


# ── What can be added ────────────────────────────────────────────────────

def test_what_can_be_added_is_offered_as_tiles(phone):
    """Stable and beta, never deprecated (`chatai`), never always on
    (`infra`), never incubating (`web`)."""
    page = phone.opens("/dashboard")

    assert page.texts("tile-name") == ["Agent", "Local AI", "Backup", "Code", "Photos"]


def test_a_tile_offers_add_and_names_the_terminal_command(phone):
    """The button is for the browser; the command is what a page without
    JavaScript shows, and what the button does."""
    page = phone.opens("/dashboard")

    assert page.texts("tile-add") == ["Add Agent", "Add Code", "Add Photos"]
    assert "stack up photos" in page.texts("tile-command")


def test_a_stacklet_that_asks_on_its_first_up_offers_only_its_command(phone):
    page = phone.opens("/dashboard")

    commands = page.texts("tile-command")
    assert {"stack up ai", "stack up backup"} <= set(commands)
    assert "Add AI" not in page.texts("tile-add")
    assert "Add Backup" not in page.texts("tile-add")


def test_the_medium_size_says_how_many_more_there_are_to_add(phone):
    assert phone.opens("/dashboard").texts("stack-more") == ["5 more to add"]


# ── The endpoint, and when the stack does not answer ─────────────────────

def test_the_widget_reads_the_same_view_as_json(phone):
    status, view = phone.reads("/dashboard/api/stack")

    assert status == 200
    assert view["state"] == "content"
    assert [r["name"] for r in view["running"]] == ["Core", "Documents", "Family memory", "Chat"]
    assert [t["id"] for t in view["to_add"]] == ["agent", "ai", "backup", "code", "photos"]


def test_when_the_host_api_does_not_answer_the_widget_says_so(monkeypatch):
    """The unit lane's default: nothing listens where the host API should."""
    import server

    phone = Browser(server.app, listener=PUBLIC_PORT, host="192.168.1.20:42000")
    status, view = phone.reads("/dashboard/api/stack")
    page = phone.opens("/dashboard")

    assert (status, view["state"]) == (503, "error")
    assert page.status == 200
    assert "The stack does not answer right now." in page.text


def test_the_page_loads_the_widgets_script_and_styles(phone):
    """What renders the widget in the browser, and makes Add work."""
    page = phone.opens("/dashboard")

    assert "/dashboard/static/widgets/fs-stack.js" in page.scripts()
    for asset in ("widgets/fs-stack.js", "widgets/fs-stack.css"):
        assert phone.opens(f"/dashboard/static/{asset}").status == 200, asset


# ── A tile follows its job ───────────────────────────────────────────────
# Add starts `stack up` as a job (`POST /dashboard/api/jobs`). While it
# runs, the tile shows its steps; when it ends, the widget reads its view
# again and the tile shows how it went, from the job and the stack's state.

class HostAPI:
    """The host API at its edge: `list` answers what the test set, and `up`
    writes two steps, waits until the test releases it, then its result."""

    def __init__(self, listing: dict):
        self.listing = listing
        self.result = {"ok": True, "stacklet": "photos", "healthy": True}
        self.runs: list[dict] = []
        self._released = threading.Event()

    def release(self) -> None:
        self._released.set()

    def call(self, request: dict, timeout: float) -> dict:
        return self.listing if request == {"cmd": "list"} else {"error": "not stood in"}

    def stream(self, request: dict, *, silence_secs: float):
        self.runs.append(request)
        yield Event.now(1, "step.done", {"text": "Rendering environment"})
        yield Event.now(2, "step.started", {"text": "Pulling images"})
        assert self._released.wait(5), "the test never released the run"
        yield Event.now(3, "step.done", {"text": "Pulling images", "started": 2})
        yield Event.now(4, "result", self.result)


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A fresh hub and jobs, as after the tools server starts, and the host
    API stand-in, with photos not added yet."""
    import live_routes

    hub = Hub()
    monkeypatch.setattr(live_routes, "hub", hub)
    monkeypatch.setattr(live_routes, "jobs", Jobs(hub))
    api = HostAPI(listing(tmp_path, monkeypatch))
    monkeypatch.setattr(client, "call", api.call)
    monkeypatch.setattr(client, "stream", api.stream)
    return api


@pytest.fixture
def browser():
    import server

    return Browser(server.app, listener=PUBLIC_PORT, host="192.168.1.20:42000")


def added(browser, host, after: dict) -> dict:
    """Click Add on photos, let the run end, and set the stack's state the
    host reports afterwards to `after`; returns the job's snapshot."""
    status, job = browser.posts("/dashboard/api/jobs", {"action": "up", "target": "photos"})
    assert status == 202, job
    host.release()
    deadline = time.monotonic() + 5
    while job["state"] == "running":
        assert time.monotonic() < deadline, "the job did not end"
        time.sleep(0.02)
        job = browser.reads(f"/dashboard/api/jobs/{job['id']}")[1]
    host.listing = after
    return job


def tile(page, sid: str) -> dict:
    """One tile as the page shows it: its state, texts and links."""
    part = page.widget("stack")
    found = [n for n in part._root.find(lambda n: n.tag == "article" and n.attrs.get("data-id") == sid)]
    assert found, f"no tile for {sid}"
    node = found[0]
    return {"state": node.attrs.get("data-status"), "text": node.text,
            "links": [(a.text, a.attrs.get("href", "")) for a in node.find(lambda n: n.tag == "a")],
            "steps": [li.text for li in node.find(lambda n: n.tag == "li")]}


def test_a_page_opened_while_a_job_runs_shows_its_steps_on_the_tile(browser, host):
    status, _ = browser.posts("/dashboard/api/jobs", {"action": "up", "target": "photos"})
    deadline = time.monotonic() + 5
    while not tile(browser.opens("/dashboard"), "photos")["steps"][1:]:
        assert time.monotonic() < deadline, "the steps never reached the page"
        time.sleep(0.02)
    shown = tile(browser.opens("/dashboard"), "photos")
    host.release()

    assert status == 202
    assert shown["state"] == "adding"
    assert shown["steps"] == ["Rendering environment", "Pulling images"]


def test_after_its_job_the_tile_says_it_runs_and_links_to_it(browser, host, tmp_path, monkeypatch):
    added(browser, host, after=listing(tmp_path, monkeypatch, running=RUNNING + ("photos",)))

    shown = tile(browser.opens("/dashboard"), "photos")

    assert shown["state"] == "running"
    assert ("Open", "http://server.lan:42010") in shown["links"]


def test_a_stacklet_that_came_up_but_does_not_answer_says_so(browser, host, tmp_path, monkeypatch):
    """Health is a property of the run (ADR-020): the job is done, and the
    tile says the stacklet does not answer yet, offers to check again and
    names the command that shows why."""
    host.result = {"ok": True, "stacklet": "photos", "healthy": False}
    job = added(browser, host, after=listing(tmp_path, monkeypatch, running=RUNNING + ("photos",),
                                             not_answering=("memory", "photos")))

    shown = tile(browser.opens("/dashboard"), "photos")

    assert job["state"] == "done"
    assert shown["state"] == "not_answering"
    assert "started, not answering yet" in shown["text"]
    assert ("Check again", "/dashboard") in shown["links"]
    assert "stack logs photos" in shown["text"]


def test_a_stacklet_that_failed_to_start_says_why_and_can_be_added_again(browser, host):
    host.result = {"error": "Failed to start services"}
    job = added(browser, host, after=host.listing)

    page = browser.opens("/dashboard")
    shown = tile(page, "photos")

    assert job["state"] == "failed"
    assert shown["state"] == "failed"
    assert "Did not start: Failed to start services" in shown["text"]
    assert "Add Photos" in page.texts("tile-add")


def test_the_small_size_says_what_changed_last(browser, host, tmp_path, monkeypatch):
    added(browser, host, after=listing(tmp_path, monkeypatch, running=RUNNING + ("photos",)))

    assert browser.opens("/dashboard").texts("stack-latest") == ["Photos was added"]


def test_without_a_job_nothing_moves_and_nothing_starts(browser, host):
    """Opening the page reads the stack's state; it starts nothing."""
    page = browser.opens("/dashboard")

    assert tile(page, "photos")["state"] == "add"
    assert host.runs == []


def test_a_tile_shows_how_its_job_ended_for_ten_minutes(tmp_path, monkeypatch):
    """After that the stacklet is listed with what runs, and a failed one is
    offered plainly again. Read through the view itself: the routes cannot
    move the clock."""
    from datetime import datetime, timedelta, timezone

    import stack_widget

    now = datetime.now(timezone.utc)
    after = listing(tmp_path, monkeypatch, running=RUNNING + ("photos",))
    job = {"id": "1f2e", "action": "up", "target": "photos", "state": "done", "steps": [],
           "outcome": {"ok": True}}

    def tiles(ended_ago, job=job, at=after):
        ended = (now - ended_ago).isoformat()
        v = stack_widget.view(at, jobs=[{**job, "ended": ended}], now=now, language="en",
                              base="/dashboard")
        return {t["id"]: t["state"] for t in v["to_add"]}

    assert tiles(timedelta(minutes=9))["photos"] == "running"
    assert "photos" not in tiles(timedelta(minutes=11))
    failed = {**job, "state": "failed", "outcome": {"error": "Failed to start services"}}
    (tmp_path / "before").mkdir()
    before = listing(tmp_path / "before", monkeypatch)
    assert tiles(timedelta(minutes=9), failed, before)["photos"] == "failed"
    assert tiles(timedelta(minutes=11), failed, before)["photos"] == "add"
