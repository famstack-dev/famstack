"""Core's start restarts the host API when the code it runs has moved on.

The host API is core's process on the Mac, run by launchd. It notes the
commit it started from in `.stack/host-api.commit`. Core's start compares
that commit with the checkout by the rule a release uses for a stale
container: commits since then that changed core or `lib/` make it old.

    host API on the checkout's code    left running ("already running")
    host API on older code             restarted ("Restarted the famstack
                                        API: its code changed")
    host API without a stamp           restarted ("Restarted the famstack
                                        API: it predates the code stamp")
    no host API answering              loaded, as on a first start

`stack list` and `stack doctor` name a host API on older code, with the
fix `stack restart core`, as they name a stale container. Without a stamp
they say nothing, because an unknown answer is not a finding there.

The hook runs as the hook runner loads it, against a checkout built for
each test: one commit, then one that changes `lib/`. `launchctl` is a
stand-in on `PATH` that notes its arguments, so the Mac's real launchd is
never touched. A listener on a free port (`STACK_API_PORT`) is the running
host API; the stand-in's `load` starts one when none runs. The plist goes
into a home folder under `tmp_path`.
"""

from __future__ import annotations

import json
import socket
import subprocess
import textwrap
import threading
import time
from pathlib import Path

import pytest

from tests.springfield import write_instance

from .conftest import REPO, StackCLI

LAUNCHCTL = textwrap.dedent('''\
    #!/bin/sh
    echo "$*" >> "{calls}"
    ''')

# `docker` as a Mac has it with core's containers running, started from
# the checkout's HEAD: the containers are current, only the host API is not.
DOCKER = textwrap.dedent('''\
    #!/bin/sh
    [ "$1" = "--context" ] && shift 2
    case "$1 $2" in
      "compose ls")  echo '[{{"Name":"stack-core","Status":"running(1)"}}]' ;;
      "context ls")  echo '{{"Name":"default","Current":true}}' ;;
      "ps "*)        printf 'stack-core\\t{commit}\\n' ;;
      *)             echo "Error response from daemon: No such container" >&2; exit 1 ;;
    esac
    ''')

RESTARTED = "Restarted the famstack API: its code changed"
PREDATES = "Restarted the famstack API: it predates the code stamp"


# ── A checkout whose code moved past the host API ────────────────────────

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path) -> dict[str, str]:
    """A checkout with two commits; the second changes the framework.

    Its core has no health check, so `stack list` asks no port."""
    root = tmp_path / "checkout"
    (root / "stacklets" / "core").mkdir(parents=True)
    (root / "lib" / "stack").mkdir(parents=True)
    (root / "stacklets" / "core" / "stacklet.toml").write_text(
        'id = "core"\nname = "Core"\ndescription = "The always-on part"\n')
    (root / "lib" / "stack" / "camping.py").write_text("TENT = 'four'\n")
    _git(tmp_path, "init", "-q", str(root))
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "the release the host API started from")
    old = _git(root, "rev-parse", "--short", "HEAD")
    (root / "lib" / "stack" / "camping.py").write_text("TENT = 'six'\n")
    _git(root, "commit", "-qam", "an update that changed the framework")
    return {"root": str(root), "old": old, "head": _git(root, "rev-parse", "--short", "HEAD")}


@pytest.fixture
def instance(tmp_path) -> Path:
    return write_instance(tmp_path)


def stamp(instance: Path, commit: str) -> None:
    """The host API started from `commit`, as it notes at start."""
    (instance / ".stack" / "host-api.commit").write_text(commit + "\n")


# ── The host API and launchd, stood in ───────────────────────────────────

class HostAPI:
    """A listener on a free port, standing in for the running host API."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]

    def listen(self):
        self.sock.listen(5)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            conn.close()

    def close(self):
        self.sock.close()


@pytest.fixture
def host_api(monkeypatch):
    api = HostAPI()
    monkeypatch.setenv("STACK_API_PORT", str(api.port))
    yield api
    api.close()


@pytest.fixture
def launchctl(tmp_path, monkeypatch, host_api):
    """`launchctl` on PATH: notes each call; `load` starts the listener
    when nothing listens yet, as launchd starts the host API."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "launchctl-calls"
    script = bin_dir / "launchctl"
    script.write_text(LAUNCHCTL.format(calls=calls))
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir), prepend=":")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    def started():
        deadline = time.time() + 10
        while time.time() < deadline:
            if calls.exists() and "load" in calls.read_text().split():
                host_api.listen()
                return
            time.sleep(0.05)
    watcher = threading.Thread(target=started, daemon=True)

    class Launchctl:
        def calls(self) -> list[str]:
            return calls.read_text().splitlines() if calls.exists() else []

        def watch(self):
            watcher.start()
    return Launchctl()


def start_core(checkout, instance) -> list[str]:
    """Core's on_start as the hook runner loads it; the steps it printed."""
    from stack.hooks import StackContext
    from stack.stack import Stack
    from stack.stacklet_code import load

    steps: list[str] = []
    stck = Stack(Path(checkout["root"]), instance.parent / "data", instance_dir=instance)
    hook = load(REPO / "stacklets" / "core", REPO / "stacklets" / "core" / "hooks" / "on_start.py")
    ctx = StackContext(stck, "core", {}, step_fn=steps.append)
    hook.run(ctx)
    return steps


# ── Core's start ─────────────────────────────────────────────────────────

class TestCoreStart:
    def test_a_host_api_on_older_code_is_restarted(self, checkout, instance, host_api, launchctl):
        stamp(instance, checkout["old"])
        host_api.listen()

        steps = start_core(checkout, instance)

        assert [c.split()[0] for c in launchctl.calls()] == ["unload", "load"]
        assert RESTARTED in steps

    def test_a_host_api_on_the_checkouts_code_keeps_running(self, checkout, instance, host_api, launchctl):
        stamp(instance, checkout["head"])
        host_api.listen()

        steps = start_core(checkout, instance)

        assert launchctl.calls() == []
        assert "famstack API already running" in steps

    def test_a_host_api_without_a_stamp_is_restarted(self, checkout, instance, host_api, launchctl):
        # Every host API from before the stamp: an update must not keep it.
        host_api.listen()

        steps = start_core(checkout, instance)

        assert [c.split()[0] for c in launchctl.calls()] == ["unload", "load"]
        assert PREDATES in steps

    def test_a_host_api_outside_a_git_tree_keeps_running(self, checkout, instance, host_api, launchctl):
        # It wrote its stamp, with no commit to name: there is nothing to compare.
        stamp(instance, "unknown")
        host_api.listen()

        steps = start_core(checkout, instance)

        assert launchctl.calls() == []
        assert "famstack API already running" in steps

    def test_no_host_api_is_loaded(self, checkout, instance, host_api, launchctl):
        launchctl.watch()

        steps = start_core(checkout, instance)

        assert "load" in [c.split()[0] for c in launchctl.calls()]
        assert "famstack API ready" in steps
        assert RESTARTED not in steps


# ── stack list and stack doctor ──────────────────────────────────────────

@pytest.fixture
def stack_cli(tmp_path, monkeypatch, as_stack_runs_it, checkout, instance, host_api):
    """`./stack` in the checkout, core's containers current, the host API listening."""
    bin_dir = tmp_path / "docker-bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(DOCKER.format(commit=checkout["head"]))
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir), prepend=":")
    host_api.listen()
    cli = StackCLI(instance, monkeypatch)
    monkeypatch.chdir(checkout["root"])
    return cli


class TestReports:
    def test_list_names_a_host_api_on_older_code(self, stack_cli, checkout, instance):
        stamp(instance, checkout["old"])

        listing = stack_cli("list", "--json").answer

        assert listing["stale"] == ["core"]
        assert listing["host_api_stale"] is True

    def test_list_is_quiet_when_the_host_api_is_current(self, stack_cli, checkout, instance):
        stamp(instance, checkout["head"])

        listing = stack_cli("list", "--json").answer

        assert listing["stale"] == []
        assert listing["host_api_stale"] is False

    def test_doctor_names_it_with_the_fix(self, stack_cli, checkout, instance):
        stamp(instance, checkout["old"])

        findings = json.loads(stack_cli("doctor", "--json").out)["findings"]

        host_api = [f for f in findings if "famstack API" in f["title"]]
        assert len(host_api) == 1
        assert host_api[0]["fix"] == "stack restart core"
