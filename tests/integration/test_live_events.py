"""The live-event chain against real Docker, on the test stacklet (ADR-020).

The unit lane proves each piece with stand-ins; this proves them together
with a real `docker compose`: `stack up --events` on stdout, the host
API's stream of it, and the tools server's job and event stream over
that, the whole chain but the browser.

A temporary stack, as in test_config_to_container.py, loads the repo's
one test stacklet as an extension (`tests/fixtures/extensions/test`). It
has no core, so `stack up` refreshes no core and posts to no room, and its
one container, `stack-test` on port 42099, collides with nothing on the
rig's Docker engine. The stacklet's switches come in through the
environment the CLI passes on: `TEST_STEP_SECS` paces its install steps,
`TEST_FAIL=install` fails its hook, `TEST_FAIL=health` makes its container
answer the health check with a 500. Every test starts from a first run:
the stacklet is destroyed after each.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
EXTENSIONS = REPO_ROOT / "tests" / "fixtures" / "extensions"
TOOLS = REPO_ROOT / "stacklets" / "core" / "tools-server"
for _path in (REPO_ROOT / "lib", TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

try:
    subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
    HAS_DOCKER = True
except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
    HAS_DOCKER = False

pytestmark = [
    pytest.mark.container_lifecycle,
    pytest.mark.skipif(not HAS_DOCKER, reason="Docker not available"),
]

# The steps the test stacklet's on_install reports, in order.
INSTALL_STEPS = ["Creating the data directory", "Writing the install marker", "on_install complete"]
HEALTH = "Health check: http://localhost:42099/"
# The stacklet's hooks run from the checkout; a run leaves no bytecode there.
NO_BYTECODE = {"PYTHONDONTWRITEBYTECODE": "1"}


# ── The temporary stack ──────────────────────────────────────────────────

class TempStack:
    """A stack root in a temporary directory, run as `python -m stack`."""

    def __init__(self, root: Path):
        self.root = root

    def env(self, **switches: str) -> dict:
        return {**os.environ, "PYTHONPATH": str(self.root / "lib"), **NO_BYTECODE, **switches}

    def run(self, *args: str, timeout: int = 120, **switches: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "stack", *args], cwd=str(self.root),
                              capture_output=True, text=True, timeout=timeout,
                              env=self.env(**switches))


@pytest.fixture(scope="module")
def temp_stack(tmp_path_factory):
    """The stack, with the test stacklet as an extension. Removed afterwards,
    container and directory both."""
    base = tmp_path_factory.mktemp("live")
    root, data = base / "stack", base / "data"
    # An empty stacklets/ is what makes the directory a stack root: without
    # one the CLI would fall back to the checkout its code comes from.
    (root / "stacklets").mkdir(parents=True)
    (root / "lib").symlink_to(REPO_ROOT / "lib")
    (root / "stack.toml").write_text(
        f'[core]\nname = "teststack"\ndomain = ""\ndata_dir = "{data}"\n'
        f'extension_dirs = ["{EXTENSIONS}"]\ntimezone = "Europe/Berlin"\n')
    (root / "users.toml").write_text(
        '[[users]]\nname = "Test Admin"\nemail = "admin@test.local"\n'
        'password = "testpass"\nrole = "admin"\n')
    (root / ".stack").mkdir()
    (root / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "testpass"\n')
    subprocess.run(["docker", "network", "create", "stack"], capture_output=True, timeout=10)

    yield TempStack(root)

    TempStack(root).run("destroy", "test", "--yes")
    subprocess.run(["docker", "rm", "-f", "stack-test"], capture_output=True, timeout=30)
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def stack(temp_stack):
    """The stack for one test, and a first run of the test stacklet in it."""
    yield temp_stack
    temp_stack.run("destroy", "test", "--yes")


def events_of(stdout: str) -> list[dict]:
    """The lines of stdout as events. Fails on a line that is not one."""
    return [json.loads(line) for line in stdout.splitlines()]


def seen(events: list[dict]) -> list[tuple[str, str | None]]:
    return [(e["type"], e["data"].get("text")) for e in events]


# ── stack up --events ────────────────────────────────────────────────────

def test_stdout_holds_only_events_in_the_order_things_happened(stack):
    """A real `docker compose` runs and reports its own progress (on the
    stderr it inherits); stdout carries the run's events and nothing else,
    the hook's steps first, then the pull, the start and the health check,
    each a start and its end."""
    run = stack.run("up", "test", "--events")

    assert run.returncode == 0, run.stderr
    events = events_of(run.stdout)
    order = seen(events)
    install = [order.index(("step.done", text)) for text in INSTALL_STEPS]
    assert install == sorted(install)
    starts = [order.index(("step.started", text))
              for text in ("Pulling images", "Starting containers", HEALTH)]
    ends = [next(i for i, e in enumerate(events) if e["data"].get("started") == events[s]["id"])
            for s in starts]
    assert install[-1] < starts[0] < ends[0] < starts[1] < ends[1] < starts[2] < ends[2]
    assert [events[i]["type"] for i in ends] == ["step.done"] * 3


def test_the_last_line_is_one_result_without_env_or_hints(stack):
    run = stack.run("up", "test", "--events")

    events = events_of(run.stdout)
    assert [e["type"] for e in events].count("result") == 1
    result = events[-1]
    assert result["type"] == "result"
    assert result["data"]["ok"] is True
    assert result["data"]["stacklet"] == "test"
    assert "env" not in result["data"] and "hints" not in result["data"]


def test_a_failing_install_hook_ends_with_an_error_result(stack):
    run = stack.run("up", "test", "--events", TEST_FAIL="install")

    events = events_of(run.stdout)
    assert run.returncode == 1
    assert ("step.failed", "on_install hook failed") in seen(events)
    assert events[-1]["type"] == "result"
    assert events[-1]["data"]["error"] == "on_install hook failed"
    assert "Starting containers" not in [text for _, text in seen(events)]


# ── The host API's stream ────────────────────────────────────────────────

@pytest.fixture
def host_api(temp_stack, monkeypatch):
    """The host API module behind a loopback listener on a free port.

    It runs the checkout's own `./stack`, as in production; only its
    working directory, `REPO_ROOT`, is the temporary stack, which is how
    the CLI finds the stack it runs. Returns the port.
    """
    path = REPO_ROOT / "stacklets" / "core" / "famstack-api.py"
    loader = importlib.machinery.SourceFileLoader("famstack_api_live", str(path))
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_file_location("famstack_api_live", path, loader=loader))
    loader.exec_module(module)
    monkeypatch.setattr(module, "REPO_ROOT", temp_stack.root)
    monkeypatch.delenv("STACK_DIR", raising=False)
    for name, value in NO_BYTECODE.items():
        monkeypatch.setenv(name, value)  # the runs the API starts inherit it

    listener = socket.create_server(("127.0.0.1", 0))

    def serve():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=module.handle_client, args=(conn,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    yield listener.getsockname()[1]
    listener.close()


@pytest.fixture
def at_the_api(host_api, monkeypatch):
    """`stack.live.client` pointed at that API, as core's env points it at the host."""
    monkeypatch.setenv("STACK_API_HOST", "127.0.0.1")
    monkeypatch.setenv("STACK_API_PORT", str(host_api))


def test_the_api_streams_a_real_run_while_it_goes_on(stack, at_the_api, monkeypatch):
    """With two seconds before each install step, the first event arrives
    seconds before the result: the API relays the run, it does not wait
    for its end."""
    from stack.live import client

    monkeypatch.setenv("TEST_STEP_SECS", "2")
    arrived = []
    for event in client.stream({"cmd": "up", "stacklet": "test"}, silence_secs=120):
        arrived.append((time.monotonic(), event))

    (first_at, _), (last_at, last) = arrived[0], arrived[-1]
    assert last_at - first_at >= 2 * len(INSTALL_STEPS) - 1
    assert last.type == "result"
    assert last.data["ok"] is True
    assert "output" not in last.data, "the API wrote this result, not the run"
    assert [e.type for _, e in arrived].count("result") == 1


# ── The whole chain but the browser ──────────────────────────────────────

@pytest.fixture
def tools_server(at_the_api, monkeypatch):
    """Core's tools server with a fresh hub and jobs, as after its start."""
    import live_routes
    import server
    from stack.live.hub import Hub
    from stack.live.jobs import Jobs

    hub = Hub()
    monkeypatch.setattr(live_routes, "hub", hub)
    monkeypatch.setattr(live_routes, "jobs", Jobs(hub))
    monkeypatch.setattr(live_routes, "HEARTBEAT_SECS", 0.2)
    return server.app


def test_a_job_started_on_the_tools_server_streams_the_real_run(stack, tools_server):
    """A container starts `up test`; a browser on the network follows it;
    the snapshot says it is done."""
    from access import INTERNAL_PORT, PUBLIC_PORT
    from tests.unit.surface.dsl import Browser

    phone = Browser(tools_server, listener=PUBLIC_PORT, host="192.168.1.20:42000")
    container = Browser(tools_server, listener=INTERNAL_PORT, host="stack-core-tools:8000")

    with phone.follows("/dashboard/api/events") as stream:
        stream.next_frame()
        status, job = container.posts("/dashboard/api/jobs", {"action": "up", "target": "test"})
        frames = stream.events_until("job.ended", within=120)

    events = [f.event for f in frames]
    assert status == 202
    assert events[0]["type"] == "job.started"
    assert events[-1]["type"] == "job.ended"
    assert {e["subject"] for e in events} == {f"job/{job['id']}"}
    steps = [e["data"].get("text") for e in events if e["type"] == "step.done"]
    assert [s for s in steps if s in INSTALL_STEPS] == INSTALL_STEPS
    assert HEALTH in steps
    _, snapshot = phone.reads(f"/dashboard/api/jobs/{job['id']}")
    assert snapshot["state"] == "done"
    assert snapshot["outcome"]["ok"] is True
    # The dashboard's "Your stack", read again as the widget does after the
    # job: the real `stack list --json`, through the host API.
    _, view = phone.reads("/dashboard/api/stack")
    assert [(r["id"], r["state"]) for r in view["running"]] == [("test", "running")]
    assert [(t["id"], t["state"]) for t in view["to_add"]] == [("test", "running")]


def test_a_stacklet_that_never_answers_is_up_and_says_so(stack, tools_server, monkeypatch):
    """The container answers its health check with a 500. Health is a
    property of the run, not a failure (ADR-020): the health step fails,
    the run is `ok` with `healthy: false`, and the job ends `done` with
    that in its outcome, as `stack up` exits 0. Waits out the stacklet's
    15 s health timeout."""
    from access import INTERNAL_PORT, PUBLIC_PORT
    from tests.unit.surface.dsl import Browser

    monkeypatch.setenv("TEST_FAIL", "health")  # the API's run inherits it
    phone = Browser(tools_server, listener=PUBLIC_PORT, host="192.168.1.20:42000")
    container = Browser(tools_server, listener=INTERNAL_PORT, host="stack-core-tools:8000")

    with phone.follows("/dashboard/api/events") as stream:
        stream.next_frame()
        _, job = container.posts("/dashboard/api/jobs", {"action": "up", "target": "test"})
        events = [f.event for f in stream.events_until("job.ended", within=120)]

    health = next(e for e in events if (e["type"], e["data"].get("text")) == ("step.started", HEALTH))
    assert [e["type"] for e in events if e["data"].get("started") == health["id"]] == ["step.failed"]
    result = next(e for e in events if e["type"] == "result")
    assert (result["data"]["ok"], result["data"]["healthy"]) == (True, False)
    _, snapshot = phone.reads(f"/dashboard/api/jobs/{job['id']}")
    assert snapshot["state"] == "done"
    assert snapshot["outcome"]["healthy"] is False
    _, view = phone.reads("/dashboard/api/stack")
    assert [(r["id"], r["state"], r["label"]) for r in view["running"]] == [
        ("test", "not_answering", "started, not answering yet")]
    assert [(t["id"], t["state"], t["command"]) for t in view["to_add"]] == [
        ("test", "not_answering", "stack logs test")]
