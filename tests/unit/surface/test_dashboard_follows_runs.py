"""The dashboard follows every run of a stacklet, wherever it was started.

A run (`stack up`, `down`, `restart`, `destroy`) writes its events to the
stacklet's run log on the Mac, `.stack/runs/<id>.jsonl`, and holds its
run lock while it goes on (`stack.runs`, `stack.run_log`). The host API
reads both: it reports each run as it starts (`runs`) and follows one
from its log (`follow`), and its streamed `up` starts the run and then
follows it the same way. Core's tools server watches the host's runs and
follows each one as a job, so a tile shows the steps of a start from the
terminal as it shows its own, and picks a run up again from the log after
the tools server restarted.

Driven through the real pieces end to end: `stack up` in a process of its
own from a checkout in `tmp_path` (the terminal, `FirstRun` of
test_one_run_per_stacklet.py, held inside its hook until the test lets
it go), the host API's own connection handler behind a loopback listener
(as in test_host_api_stream.py), and the tools server's FastAPI app,
started as uvicorn starts it so its lifespan watches the runs. A browser
reads the live events through the poll route. Docker is the one stand-in,
a `docker` first on PATH.

The waits are shortened for the tests: how often a follower reads the
log, how often the host API looks for a new run, the heartbeat of its
feed, the routes' waits.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import socket
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from .conftest import LIB, STACKLETS
from .dsl import Browser
from .test_one_run_per_stacklet import HOLD_UNTIL_TOLD, FirstRun

TOOLS = STACKLETS / "core" / "tools-server"
for _path in (LIB, TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from access import INTERNAL_PORT, PUBLIC_PORT  # noqa: E402
from stack.live import follow  # noqa: E402
from stack.live.hub import Hub  # noqa: E402
from stack.live.jobs import Jobs  # noqa: E402

REPO = Path(__file__).resolve().parents[3]

# How long a test waits for what a run does, at most. A `stack up` is a
# Python process of its own; under the parallel lane its start alone can
# take a second.
WITHIN = 30


# ── The Mac: a checkout, its `./stack`, the host API ─────────────────────

@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A checkout with one stacklet that was never up, `app`, whose first
    run reports "Holding the run" and can be held there. Its `./stack`
    runs this repo's CLI, and a `docker` that does nothing is first on
    PATH, for every process the test starts. Returns its directory."""
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\ntimezone = "Europe/Berlin"\n')
    (tmp_path / ".stack").mkdir()
    (tmp_path / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')
    app = tmp_path / "stacklets" / "app"
    (app / "hooks").mkdir(parents=True)
    (app / "docker-compose.yml").write_text("services:\n  app:\n    image: busybox\n")
    (app / "stacklet.toml").write_text('id = "app"\nname = "App"\n')
    (app / "hooks" / "on_install.py").write_text(HOLD_UNTIL_TOLD)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "docker").chmod(0o755)
    (tmp_path / "stack").write_text(textwrap.dedent(f"""\
        #!/bin/sh
        exec env PYTHONPATH="{REPO / 'lib'}" "{sys.executable}" -m stack "$@"
    """))
    (tmp_path / "stack").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.delenv("STACK_DIR", raising=False)
    monkeypatch.delenv("STACK_RUN_SURFACE", raising=False)
    monkeypatch.delenv("HOLD_RUN", raising=False)
    return tmp_path


@pytest.fixture
def host_api(checkout, monkeypatch):
    """The host API of this checkout, listening on loopback, one thread per
    connection as its `main` does. Core's containers reach it through the
    address in their environment, which the tools server's client reads."""
    path = STACKLETS / "core" / "famstack-api.py"
    loader = importlib.machinery.SourceFileLoader("famstack_api_runs", str(path))
    spec = importlib.util.spec_from_file_location("famstack_api_runs", path, loader=loader)
    api = importlib.util.module_from_spec(spec)
    loader.exec_module(api)
    monkeypatch.setattr(api, "REPO_ROOT", checkout)
    monkeypatch.setattr(api, "STACK_BIN", checkout / "stack")
    monkeypatch.setattr(api, "RUNS_HEARTBEAT_SECS", 0.2)
    monkeypatch.setattr(follow, "POLL_SECS", 0.05)
    monkeypatch.setattr(follow, "WATCH_SECS", 0.05)

    listener = socket.create_server(("127.0.0.1", 0))

    def serve():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=api.handle_client, args=(conn,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setenv("STACK_API_HOST", "127.0.0.1")
    monkeypatch.setenv("STACK_API_PORT", str(listener.getsockname()[1]))
    yield api
    listener.close()


# ── Core's tools server ──────────────────────────────────────────────────

class ToolsServer:
    """Core's tools server as a process: started with its lifespan, so it
    watches the host's runs, and restarted with a fresh hub and fresh jobs,
    as a recreated container starts."""

    def __init__(self, monkeypatch):
        import live_routes
        import server

        self.routes = live_routes
        self.app = server.app
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(live_routes, "HEARTBEAT_SECS", 0.1)
        monkeypatch.setattr(live_routes, "POLL_WAIT_SECS", 0.2)
        monkeypatch.setattr(live_routes, "WATCH_RETRY_SECS", 0.1)
        self._running = None

    def start(self) -> None:
        from fastapi.testclient import TestClient

        hub = Hub()
        self.monkeypatch.setattr(self.routes, "hub", hub)
        self.monkeypatch.setattr(self.routes, "jobs", Jobs(hub))
        self._running = TestClient(self.app)
        self._running.__enter__()

    def stop(self) -> None:
        if self._running is not None:
            self._running.__exit__(None, None, None)
            self._running = None

    @property
    def jobs(self) -> Jobs:
        return self.routes.jobs

    @property
    def everything(self) -> str:
        """The cursor before this process's first event."""
        return f"{self.routes.hub.epoch}.0"


@pytest.fixture
def tools(host_api, monkeypatch):
    server = ToolsServer(monkeypatch)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def phone(tools) -> Browser:
    """A phone on the home network, opening the Mac's published port."""
    return Browser(tools.app, listener=PUBLIC_PORT, host="192.168.1.20:42000")


@pytest.fixture
def container(tools) -> Browser:
    """One of the stack's containers, on the internal listener (the page's Start)."""
    return Browser(tools.app, listener=INTERNAL_PORT, host="stack-core-tools:8000")


def events_until(browser: Browser, after: str, done, within: float = WITHIN) -> tuple[list[dict], str]:
    """The live events after `after`, read as the poll fallback reads them,
    until `done(events)` holds; and the cursor to read on from."""
    deadline = time.monotonic() + within
    seen: list[dict] = []
    while not done(seen):
        assert time.monotonic() < deadline, f"not there after {within} s: {_shown(seen)}"
        status, page = browser.reads(f"/dashboard/api/poll?after={after}")
        assert status == 200, page
        seen += page["events"]
        after = page["cursor"]
    return seen, after


def _shown(events: list[dict]) -> list[tuple]:
    return [(e["type"], e["data"].get("text") or e["data"].get("target")) for e in events]


def step(text: str):
    """`done` for `events_until`: a step with this text arrived."""
    return lambda events: any(e["type"].startswith("step.") and e["data"].get("text") == text
                              for e in events)


def ended(events: list[dict]) -> bool:
    return any(e["type"] == "job.ended" for e in events)


def texts(events: list[dict]) -> list[str]:
    return [e["data"]["text"] for e in events if e["type"].startswith("step.")]


def run_log(root: Path, stacklet: str = "app") -> list[dict]:
    path = root / ".stack" / "runs" / f"{stacklet}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


# ── A run from the terminal ──────────────────────────────────────────────

def test_a_start_from_the_terminal_shows_its_steps_and_result_live(checkout, tools, phone):
    """Given `stack up app` in a terminal, when the dashboard is open,
    then the live events carry a job for that run: its start, the hook's
    step while the run goes on, and its result at the end."""
    with FirstRun(checkout) as terminal:
        during, cursor = events_until(phone, tools.everything, step("Holding the run"))
        running = [job.snapshot() for job in tools.jobs.running()]
        assert terminal.finish() == 0
        after, _ = events_until(phone, cursor, ended)

    started = [e for e in during if e["type"] == "job.started"]
    assert [(e["data"]["action"], e["data"]["target"]) for e in started] == [("up", "app")]
    assert [(j["action"], j["target"]) for j in running] == [("up", "app")]
    job = after[-1]["data"]
    assert job["state"] == "done"
    assert job["outcome"]["ok"] is True
    assert [s["text"] for s in job["steps"]].count("Holding the run") == 1
    assert [e["type"] for e in during + after].count("result") == 1


def test_a_run_that_stopped_ends_its_job_and_offers_start_again(checkout, tools, phone):
    """Given a run from the terminal that is killed (a crash, Ctrl-C), then
    its job ends as failed, from the free lock, saying the run stopped."""
    with FirstRun(checkout) as terminal:
        _, cursor = events_until(phone, tools.everything, step("Holding the run"))
        terminal.kill()
        after, _ = events_until(phone, cursor, ended)

    job = after[-1]["data"]
    assert job["state"] == "failed"
    assert "stopped" in job["outcome"]["error"]
    assert not tools.jobs.running()


# ── The tools server restarts while a run goes on ────────────────────────

def test_after_a_restart_the_job_goes_on_from_the_run_log(checkout, tools, phone):
    """Given a run going on, when the tools server restarts (core's
    refresh), then a page that comes back is told to read its view again,
    the job is there again with the steps so far, and what the page shows
    (the snapshot, then the events after it) has each step once, and the
    run's end."""
    with FirstRun(checkout) as terminal:
        _, old_cursor = events_until(phone, tools.everything, step("Holding the run"))
        tools.stop()
        tools.start()
        back, cursor = events_until(phone, old_cursor, bool)
        snapshot = job_with_step(tools, "Holding the run")
        assert terminal.finish() == 0
        after, _ = events_until(phone, cursor, ended)

    assert back[0]["type"] == "resync"
    assert after[-1]["data"]["state"] == "done"
    assert lines_shown(snapshot, back + after).count("Holding the run") == 1


def job_with_step(tools: ToolsServer, text: str, within: float = WITHIN) -> dict:
    """The snapshot of the running job once it has a step with `text`, as
    the page reads it when it reloads its view."""
    deadline = time.monotonic() + within
    while True:
        for job in tools.jobs.running():
            snapshot = job.snapshot()
            if text in [s["text"] for s in snapshot["steps"]]:
                return snapshot
        assert time.monotonic() < deadline, f"no running job with {text!r} after {within} s"
        time.sleep(0.02)


def lines_shown(snapshot: dict, events: list[dict]) -> list[str]:
    """The step lines a tile shows: the snapshot's steps, then one line for
    each event that begins a step and is not one of them (fs-stack.js)."""
    lines = {s["id"]: s["text"] for s in snapshot["steps"]}
    for e in events:
        begins = e["type"] == "step.started" or (e["type"].startswith("step.")
                                                  and not e["data"].get("started"))
        if begins and e["id"] not in lines:
            lines[e["id"]] = e["data"]["text"]
    return list(lines.values())


# ── Start on the dashboard ───────────────────────────────────────────────

def test_start_on_a_tile_is_followed_from_the_run_log(checkout, tools, phone, container):
    """Given Start on the App tile, then the run it starts is followed the
    same way: one job, the run's steps and its result, and the run log
    names the dashboard as the surface."""
    status, job = container.posts("/dashboard/api/jobs", {"action": "up", "target": "app"})
    assert status == 202, job
    events, _ = events_until(phone, tools.everything, ended)

    ends = [e["data"] for e in events if e["type"] == "job.ended"]
    assert [j["id"] for j in ends] == [job["id"]]
    assert ends[0]["state"] == "done", ends[0]
    assert "Starting containers" in [s["text"] for s in ends[0]["steps"]]
    assert run_log(checkout)[0]["data"]["surface"] == "dashboard"
    assert len(tools.jobs.recent()) == 1


def test_start_while_the_terminal_runs_it_follows_that_run(checkout, host_api, monkeypatch):
    """Given `stack up app` in a terminal, when the host API is asked to
    start app as well, then no second run starts: the caller follows the
    terminal's run to its result."""
    from stack.live import client

    with FirstRun(checkout) as terminal:
        events = client.stream({"cmd": "up", "stacklet": "app"}, silence_secs=WITHIN)
        first = []
        while not first or first[-1].data.get("text") != "Holding the run":
            first.append(next(events))
        assert terminal.finish() == 0
        rest = list(events)

    assert first[0].type == "run"
    assert first[0].data["surface"] == "terminal"
    assert [e.type for e in rest].count("result") == 1
    assert rest[-1].type == "result"
    assert rest[-1].data["ok"] is True


# ── At rest ──────────────────────────────────────────────────────────────

def test_with_no_run_going_on_there_is_no_job(checkout, tools, phone):
    """Given no run going on and an old run's log, when the tools server
    starts, then it starts no job and publishes nothing."""
    (checkout / ".stack" / "runs").mkdir()
    (checkout / ".stack" / "runs" / "app.jsonl").write_text(
        json.dumps({"id": 1, "type": "run", "time": "2026-11-09T18:40:12.000Z",
                    "data": {"stacklet": "app", "surface": "terminal", "pid": 1,
                             "started": "2026-11-09T19:40:12+01:00", "action": "up"}}) + "\n")
    tools.stop()
    tools.start()
    time.sleep(0.5)

    status, page = phone.reads(f"/dashboard/api/poll?after={tools.everything}")
    assert status == 200
    assert page["events"] == []
    assert tools.jobs.recent() == []
