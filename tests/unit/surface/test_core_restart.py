"""`./stack restart core` keeps the host API running.

Core's host API is a process on the Mac that launchd runs from the agent
`dev.famstack.api`; the dashboard's runs and the setup page's jobs go
through it. A restart of core stops the agent and loads it again within
the same second. launchd lets a stopped job go only once its process has
ended: until then the old host API still answers on its port, and a
`launchctl load` of the same label prints `Load failed: 5: Input/output
error`, loads nothing, and exits 0. A start that saw the old process
answer, or trusted the load's exit code, left launchd without the host
API once that process ended.

The commands run through `./stack` in this process (`StackCLI`), on a
Simpsons instance, with core's own hooks. Docker is stood in for in
process, as in `test_update_pairs_admin.py`, and core's health check
answers. `launchctl` is a launchd that remembers its jobs and lets a
stopped one go as a real one does (`launchd_stand_in.py`). The host API
is a listener on a free port (`STACK_API_PORT`) that is open exactly while
launchd has the job, leaving or not, as the process launchd runs is.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from pathlib import Path

import pytest

from stack import host_api

from tests.unit.stacklets.launchd_stand_in import launchd

from .conftest import StackCLI, write_instance

LABEL = "dev.famstack.api"


class HostAPI:
    """The host API as the process launchd runs: it listens on `port`
    while launchd lists its job, and notes its start as the real one does
    (`host_api.record_start`). A thread follows launchd's jobs folder."""

    POLL_SECONDS = 0.01

    def __init__(self, jobs: Path, instance: Path):
        self.jobs, self.instance = jobs, instance
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self._listener: socket.socket | None = None
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._follow, daemon=True)

    def answers(self) -> bool:
        with contextlib.suppress(OSError), \
                socket.create_connection(("127.0.0.1", self.port), timeout=1):
            return True
        return False

    def _launchd_runs_it(self) -> bool:
        return (self.jobs / LABEL).exists() or (self.jobs / f"{LABEL}.leaving").exists()

    def _follow(self) -> None:
        while not self._done.is_set():
            runs = self._launchd_runs_it()
            if runs and self._listener is None:
                listener = socket.socket()
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("127.0.0.1", self.port))
                listener.listen()
                self._listener = listener
                host_api.record_start(self.instance, "unknown")
            elif not runs and self._listener is not None:
                self._listener.close()
                self._listener = None
            time.sleep(self.POLL_SECONDS)

    def __enter__(self) -> HostAPI:
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._done.set()
        self._thread.join()
        if self._listener is not None:
            self._listener.close()


class Mac:
    """The Simpsons' Mac: `./stack` on its instance, launchd's jobs, and
    the host API launchd runs."""

    def __init__(self, stack: StackCLI, jobs: Path, home: Path, api: HostAPI):
        self.stack, self.jobs, self.home, self.api = stack, jobs, home, api

    def launchd_runs_the_host_api(self) -> bool:
        """launchd has the job, not one that is leaving."""
        return (self.jobs / LABEL).exists() and not (self.jobs / f"{LABEL}.leaving").exists()

    def agent_file(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"


@pytest.fixture
def mac(tmp_path, monkeypatch, as_stack_runs_it, _stand_in_folder) -> Mac:
    bin_dir, jobs = launchd(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", f"{bin_dir}:{_stand_in_folder}:/usr/bin:/bin")
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    instance = write_instance(tmp_path)
    for name, answer in {
        "check_docker": ("Docker is running", None),
        "init_runtime": ("orbstack", None),
        "ensure_network": ("mocked", None),
        "compose_pull": None,
        "compose_build": (0, ""),
        "compose_up": (0, ""),
        "compose_stop": (0, ""),
        "compose_down": (0, ""),
        "running_project_ids": {"core"},
        "container_commits": {},
        # Core's health check, the dashboard in its container.
        "probe_health": "ready",
    }.items():
        monkeypatch.setattr(f"stack.docker.{name}", lambda *_a, _answer=answer, **_k: _answer)
    with HostAPI(jobs, instance) as api:
        monkeypatch.setenv("STACK_API_PORT", str(api.port))
        yield Mac(StackCLI(instance, monkeypatch), jobs, home, api)


def test_a_restart_keeps_the_host_api_running(mac):
    """Given core runs, when `./stack restart core` runs twice in a row,
    then launchd runs the host API after each, it answers on its port, and
    its agent file is there for the next login. A restart of core is what
    `./stack restart` runs after every update that changes the stack's
    code. Each run takes about a second, as the stack's own start does."""
    up = mac.stack("up", "core")
    assert up.code == 0, up.err + up.out
    assert mac.launchd_runs_the_host_api()

    for _ in range(2):
        restarted = mac.stack("restart", "core")

        assert restarted.code == 0, restarted.err + restarted.out
        assert mac.launchd_runs_the_host_api()
        assert mac.api.answers()
        assert mac.agent_file().exists()


def test_a_stop_returns_once_launchd_let_the_host_api_go(mac):
    """Given core runs, when `./stack down core` ends, then launchd no
    longer lists the host API, and nothing answers on its port."""
    mac.stack("up", "core")

    down = mac.stack("down", "core")

    assert down.code == 0, down.err + down.out
    assert sorted(p.name for p in mac.jobs.iterdir()) == []
    assert not mac.agent_file().exists()
