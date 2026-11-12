"""The dashboard's "Your stack": a status pill per set-up service, live.

The dashboard reads the stack's state from the host API (`stack list
--json`) and shows one status pill per set-up service, each with its
status, and behind it the service's service card. A start of a service
(`POST /dashboard/api/jobs`, or any run the tools server follows) shows on
its pill and service card while it goes on, and how it ended once it did.
The rules of a tile, which the setup page's service cards are built on,
are pinned through `stack_widget.tile`, where the routes cannot move the
clock.

Driven through the tools server's FastAPI app as a phone on the home
network opens it (the Browser DSL). The host API is the stand-in, at its
edge: `stack.live.client.call` answers `list` with what the real
`Stack.list()` reports over the repo's own stacklets, with Docker stood
in for (which containers run, which health checks pass). The page is
read as served, without JavaScript. `test_dashboard_status_pills.py`
covers the pills' states and the link to what is left.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time

import pytest

from .conftest import LIB, REPO, STACKLETS
from .dsl import Browser

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

TOOLS = STACKLETS / "core" / "tools-server"
for _path in (LIB, TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from access import CARDS_PORT, PUBLIC_PORT  # noqa: E402
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


def pills(page) -> list[tuple[str, str]]:
    """(service, status) of every status pill, in page order."""
    return re.findall(r'<a class="fs-status" [^>]*data-service="(\w+)"[^>]*>.*?'
                      r'class="service-card-status" data-status="(\w+)"', page.body, re.S)


def service_card(page, sid: str) -> dict:
    """The service card a pill opens: its status, texts, links and steps."""
    found = page._root.find(lambda n: n.tag == "article" and n.attrs.get("data-service") == sid)
    assert found, f"no service card for {sid}"
    node = found[0]
    return {"state": node.attrs.get("data-status"), "text": node.text,
            "links": [(a.text, a.attrs.get("href", "")) for a in node.find(lambda n: n.tag == "a")],
            "steps": [li.text for li in node.find(lambda n: n.tag == "li")]}


# ── What runs ────────────────────────────────────────────────────────────

def test_each_set_up_service_has_a_pill_with_its_state(phone):
    """Core is the stack's own and has none; the memory, whose check fails,
    says it does not answer."""
    assert pills(phone.opens("/dashboard")) == [
        ("messages", "running"), ("docs", "running"), ("memory", "not_answering")]


def test_code_has_no_pill_when_it_runs(tmp_path, monkeypatch):
    """Code keeps the memory's pages and is set up by the memory's start;
    it has no pill. `stack list` still lists it."""
    import server

    answer = listing(tmp_path, monkeypatch, running=(*RUNNING, "code"))
    monkeypatch.setattr(client, "call", lambda request, timeout: answer)
    page = Browser(server.app, listener=PUBLIC_PORT, host="192.168.1.20:42000").opens("/dashboard")

    assert "code" in {s["id"] for s in answer["stacklets"]}
    assert "code" not in dict(pills(page))
    assert 'data-service="code"' not in page.body


def test_when_the_host_api_does_not_answer_the_pills_say_so(monkeypatch):
    """The unit lane's default: nothing listens where the host API should."""
    import server

    phone = Browser(server.app, listener=PUBLIC_PORT, host="192.168.1.20:42000")
    status, view = phone.reads("/dashboard/api/stack")
    page = phone.opens("/dashboard")

    assert (status, view["state"]) == (503, "error")
    assert page.status == 200
    assert page.widget("status").attribute("fs-widget", "state") == "error"


def test_the_page_loads_the_pills_script_and_styles(phone):
    """What opens a service card in the browser and follows a run."""
    page = phone.opens("/dashboard")

    assert "/dashboard/static/widgets/fs-status.js" in page.scripts()
    for asset in ("widgets/fs-status.js", "widgets/fs-status.css", "widgets/service-cards.js",
                  "widgets/fs-stack.js", "components/service-card.css"):
        assert phone.opens(f"/dashboard/static/{asset}").status == 200, asset


def test_the_ai_opens_the_admin_page_of_its_engine(tmp_path, monkeypatch):
    """oMLX serves nothing at its root, so Open goes to its admin page; a
    service that names no page opens at its root."""
    import server

    answer = listing(tmp_path, monkeypatch, running=(*RUNNING, "ai"))
    monkeypatch.setattr(client, "call", lambda request, timeout: answer
                        if request == {"cmd": "list"} else {"error": "not stood in"})
    page = Browser(server.app, listener=PUBLIC_PORT, host="192.168.1.20:42000").opens("/dashboard")

    assert ("Open", "http://server.lan:42060/admin") in service_card(page, "ai")["links"]
    assert ("Open Paperless", "http://server.lan:42020") in service_card(page, "docs")["links"]


# ── A start shows on its pill ────────────────────────────────────────────
# A start is a job (`POST /dashboard/api/jobs`, or a run the tools server
# follows from the run log). While it runs, the service has a pill that
# says Starting, and its service card the steps; when it ended, the page
# read again shows how it went, from the job and the stack's state.

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
    """The Mac's own browser, on the Mac's address, where a start is made."""
    import server

    return Browser(server.app, listener=CARDS_PORT, host="127.0.0.1:42002")


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


def test_a_page_opened_while_a_start_runs_shows_it_starting_with_its_steps(browser, host):
    status, _ = browser.posts("/dashboard/api/jobs", {"action": "up", "target": "photos"})
    deadline = time.monotonic() + 5
    while not service_card(browser.opens("/dashboard"), "photos")["steps"][1:]:
        assert time.monotonic() < deadline, "the steps never reached the page"
        time.sleep(0.02)
    page = browser.opens("/dashboard")
    host.release()

    assert status == 202
    assert ("photos", "starting") in pills(page)
    assert service_card(page, "photos")["steps"] == ["Rendering environment", "Pulling images"]


def test_after_its_start_the_pill_says_it_runs_and_its_service_card_opens_it(
        browser, host, tmp_path, monkeypatch):
    added(browser, host, after=listing(tmp_path, monkeypatch, running=RUNNING + ("photos",)))
    page = browser.opens("/dashboard")

    assert ("photos", "running") in pills(page)
    assert ("Open", "http://localhost:42010") in service_card(page, "photos")["links"]


def test_a_service_that_came_up_but_does_not_answer_says_so(browser, host, tmp_path, monkeypatch):
    """Health is a property of the run (ADR-020): the job is done, and the
    pill says the service does not answer; its service card names the
    command that shows why."""
    host.result = {"ok": True, "stacklet": "photos", "healthy": False}
    job = added(browser, host, after=listing(tmp_path, monkeypatch, running=RUNNING + ("photos",),
                                             not_answering=("memory", "photos")))
    page = browser.opens("/dashboard")

    assert job["state"] == "done"
    assert ("photos", "not_answering") in pills(page)
    assert "stack logs photos" in service_card(page, "photos")["text"]


def test_a_first_start_that_failed_leaves_no_pill(browser, host):
    """Photos never came up, so it is not set up: no pill. /setup offers
    it again."""
    host.result = {"error": "Failed to start services"}
    job = added(browser, host, after=host.listing)

    assert job["state"] == "failed"
    assert "photos" not in dict(pills(browser.opens("/dashboard")))


def test_without_a_start_nothing_moves_and_nothing_starts(browser, host):
    """Opening the page reads the stack's state; it starts nothing."""
    page = browser.opens("/dashboard")

    assert "photos" not in dict(pills(page))
    assert host.runs == []


# ── The rules of a tile ──────────────────────────────────────────────────
# What the setup page's service cards are built on (`stack_widget.tile`).

def tiles(stacklets: dict, jobs: list[dict], now, language: str = "en") -> dict[str, dict]:
    """Each tile the setup offers or follows, by its stacklet, as `/setup` builds them."""
    import stack_widget

    shown = stack_widget.shown_jobs(jobs, now)
    on = stack_widget.on_screens(stacklets["stacklets"])
    return {s["id"]: stack_widget.tile(s, on, shown.get(s["id"]), language)
            for s in on if stack_widget.offered(s) or s["id"] in shown}


def test_a_tile_shows_how_its_start_ended_for_ten_minutes(tmp_path, monkeypatch):
    """After that the stacklet is listed with what runs, and a failed one is
    offered plainly again."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    after = listing(tmp_path, monkeypatch, running=RUNNING + ("photos",))
    job = {"id": "1f2e", "action": "up", "target": "photos", "state": "done", "steps": [],
           "outcome": {"ok": True}}

    def state(ended_ago, job=job, at=after):
        shown = tiles(at, [{**job, "ended": (now - ended_ago).isoformat()}], now)
        return shown["photos"]["state"] if "photos" in shown else None

    assert state(timedelta(minutes=9)) == "running"
    assert state(timedelta(minutes=11)) is None
    failed = {**job, "state": "failed", "outcome": {"error": "Failed to start services"}}
    (tmp_path / "before").mkdir()
    before = listing(tmp_path / "before", monkeypatch)
    assert state(timedelta(minutes=9), failed, before) == "failed"
    assert state(timedelta(minutes=11), failed, before) == "add"


def test_a_started_tile_says_by_name_that_its_stacklet_started(tmp_path, monkeypatch):
    """The tile whose start was pressed says that it worked, in place."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    after = listing(tmp_path, monkeypatch, running=RUNNING + ("photos",))
    job = {"id": "1f2e", "action": "up", "target": "photos", "state": "done", "steps": [],
           "outcome": {"ok": True}, "ended": (now - timedelta(minutes=1)).isoformat()}

    assert tiles(after, [job], now)["photos"]["label"] == "Photos started successfully"
    assert tiles(after, [job], now, "de")["photos"]["label"] == "Fotos wurde gestartet"
