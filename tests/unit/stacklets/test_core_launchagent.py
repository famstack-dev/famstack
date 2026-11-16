"""Core's LaunchAgent exists only while core is up.

core's on_start writes `dev.famstack.api` into ~/Library/LaunchAgents
with RunAtLoad, so launchd starts the host API at every login. Once
core is down, destroyed or uninstalled, nothing may start it again: a
login after `down` would bring the API back while core reports down,
and one after `destroy` would respawn a wrapper whose data dir is gone.

The hooks run through the framework's own down and destroy, against a
copy of the core stacklet and a temporary home. launchctl is a launchd
that remembers its jobs and records its calls (`launchd_stand_in.py`);
the tests read what launchd has and what is left on disk for it to find
at the next login.
"""

from __future__ import annotations

import importlib.util
import plistlib
import shutil
import socket
from pathlib import Path

import pytest

from stack import Stack, host_api
from stack.hooks import build_hook_ctx

from tests.unit.stacklets.launchd_stand_in import launchd

REPO_ROOT = Path(__file__).resolve().parents[3]
CORE = REPO_ROOT / "stacklets" / "core"

_spec = importlib.util.spec_from_file_location("core_on_start", CORE / "hooks" / "on_start.py")
on_start = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(on_start)


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


@pytest.fixture
def launchctl(tmp_path, monkeypatch):
    """A launchd on PATH that remembers its jobs; the file of its calls."""
    bin_dir, _jobs = launchd(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return tmp_path / "calls"


@pytest.fixture
def jobs(tmp_path, launchctl) -> Path:
    """The jobs launchd has, one file each (`launchd_stand_in.py`)."""
    return tmp_path / "launchd"


@pytest.fixture(autouse=True)
def api_answering(monkeypatch):
    """Something listening where on_start probes for the API.

    on_start then takes its "already running" path instead of waiting
    five seconds for an API that launchctl, stubbed, never starts.
    """
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        monkeypatch.setattr(on_start, "API_PORT", listener.getsockname()[1])
        yield


@pytest.fixture
def repo(tmp_path):
    """A checkout holding a copy of core, so destroy removes nothing real."""
    repo = tmp_path / "repo"
    shutil.copytree(CORE, repo / "stacklets" / "core",
                    ignore=shutil.ignore_patterns(".env", "__pycache__"))
    return repo


def _instance(tmp_path, repo, name) -> Stack:
    instance = tmp_path / name
    (instance / ".stack").mkdir(parents=True)
    (instance / ".stack" / "secrets.toml").write_text("")
    data = tmp_path / f"{name}-data"
    (instance / "stack.toml").write_text(f'[core]\ndomain = ""\ndata_dir = "{data}"\n')
    return Stack(root=repo, data=data, instance_dir=instance)


@pytest.fixture
def stack(tmp_path, repo, home, launchctl):
    return _instance(tmp_path, repo, "instance")


def _up(stack: Stack):
    # The API answering on the port noted its start, as a real one does;
    # this checkout is no git tree, so it names no commit.
    host_api.record_start(stack.instance_dir, "unknown")
    on_start.run(build_hook_ctx("core", stack=stack))


def _plist(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / "dev.famstack.api.plist"


def _changes(launchctl: Path) -> list[str]:
    """The launchctl calls that change what launchd runs: all but `list`."""
    return [c for c in launchctl.read_text().splitlines() if not c.startswith("launchctl list")]


# ── Behaviour ─────────────────────────────────────────────────────────────

def test_up_leaves_an_agent_that_starts_at_login(stack, home):
    _up(stack)

    agent = plistlib.loads(_plist(home).read_bytes())
    assert agent["RunAtLoad"] is True
    assert agent["ProgramArguments"] == [str(stack.data / "core" / "famstack-api")]


def test_the_api_runs_commands_with_the_systems_own_tools(stack, home):
    """launchd gives the agent no PATH, so the wrapper sets one, and every
    command the host API runs inherits it: a dashboard's Add, its setup
    forms, the stacker bot's `stack up`. Hooks call macOS's own tools, and
    those live in the system folders: `mount` in /sbin (backup's disk
    question) and `diskutil` in /usr/sbin (its encryption check). Without
    them the backup's questions failed from the dashboard and only the
    terminal command was offered (the e2e laptop, 2026-10-27)."""
    _up(stack)

    wrapper = (stack.data / "core" / "famstack-api").read_text()
    exported = next(line for line in wrapper.splitlines() if line.startswith("export PATH="))
    folders = exported.removeprefix("export PATH=").strip('"').split(":")
    assert {"/sbin", "/usr/sbin", "/usr/bin", "/bin"} <= set(folders)
    assert folders[0] == "/opt/homebrew/bin", "Homebrew's python3 and tools come first"


def _wrapper(stack: Stack) -> str:
    """The program core's agent runs for this instance."""
    return str(stack.data / "core" / "famstack-api")


def test_down_stops_the_agent_and_leaves_nothing_for_the_next_login(stack, home, jobs):
    _up(stack)
    (jobs / "dev.famstack.api").write_text(_wrapper(stack))   # launchd runs the host API

    stack.down("core")

    assert list(jobs.iterdir()) == []
    assert not _plist(home).exists()


@pytest.mark.parametrize("agent_file", ["missing", "unreadable"])
def test_down_stops_the_host_api_launchd_runs_whatever_its_agent_file_says(
        stack, home, jobs, agent_file):
    """Given launchd runs this instance's host API and its agent file is
    gone or unreadable, when core goes down, then launchd no longer has
    the job and no agent file is left: launchd is asked by the label, and
    what it runs decides, not the file."""
    _up(stack)
    (jobs / "dev.famstack.api").write_text(_wrapper(stack))
    if agent_file == "missing":
        _plist(home).unlink()
    else:
        _plist(home).write_text("<plist><dict>")

    assert stack.down("core")["ok"]

    assert list(jobs.iterdir()) == []
    assert not _plist(home).exists()


def test_up_after_down_brings_the_agent_back(stack, home):
    _up(stack)
    stack.down("core")

    _up(stack)

    assert plistlib.loads(_plist(home).read_bytes())["RunAtLoad"] is True


def test_destroy_leaves_no_agent_behind(stack, home):
    """uninstall destroys each stacklet, so this covers it as well."""
    _up(stack)

    stack.destroy("core")

    assert not (stack.data / "core").exists()
    assert not _plist(home).exists()


def test_repeating_down_and_destroy_changes_nothing(stack, home, launchctl):
    _up(stack)
    stack.down("core")
    launchctl.write_text("")

    assert stack.down("core")["ok"]
    assert stack.destroy("core")["ok"]
    assert stack.destroy("core")["ok"]

    assert _changes(launchctl) == []
    assert not _plist(home).exists()


def test_the_agent_another_instance_wrote_is_left_alone(stack, tmp_path, repo, home, launchctl):
    """Same label, other data dir: that API belongs to the other instance."""
    _up(stack)
    other = _instance(tmp_path, repo, "other")
    _up(other)
    (tmp_path / "launchd" / "dev.famstack.api").write_text(_wrapper(other))
    written = _plist(home).read_bytes()

    stack.down("core")
    stack.destroy("core")

    assert _plist(home).read_bytes() == written
    assert _changes(launchctl) == []
