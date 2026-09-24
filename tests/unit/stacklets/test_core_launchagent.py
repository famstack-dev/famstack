"""Core's LaunchAgent exists only while core is up.

core's on_start writes `dev.famstack.api` into ~/Library/LaunchAgents
with RunAtLoad, so launchd starts the host API at every login. Once
core is down, destroyed or uninstalled, nothing may start it again: a
login after `down` would bring the API back while core reports down,
and one after `destroy` would respawn a wrapper whose data dir is gone.

The hooks run through the framework's own down and destroy, against a
copy of the core stacklet and a temporary home. launchctl is a stub on
PATH that records its calls; the tests read what is left on disk for
launchd to find at the next login.
"""

from __future__ import annotations

import importlib.util
import plistlib
import shutil
import socket
from pathlib import Path

import pytest

from stack import Stack
from stack.hooks import build_hook_ctx

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
    """A launchctl on PATH that only records its arguments."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "launchctl.log"
    log.touch()
    stub = bin_dir / "launchctl"
    stub.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\n')
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return log


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
    on_start.run(build_hook_ctx("core", stack=stack))


def _plist(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / "dev.famstack.api.plist"


def _calls(launchctl: Path) -> list[str]:
    return launchctl.read_text().splitlines()


# ── Behaviour ─────────────────────────────────────────────────────────────

def test_up_leaves_an_agent_that_starts_at_login(stack, home):
    _up(stack)

    agent = plistlib.loads(_plist(home).read_bytes())
    assert agent["RunAtLoad"] is True
    assert agent["ProgramArguments"] == [str(stack.data / "core" / "famstack-api")]


def test_down_unloads_the_agent_and_leaves_nothing_for_the_next_login(stack, home, launchctl):
    _up(stack)

    stack.down("core")

    assert f"unload {_plist(home)}" in _calls(launchctl)
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

    assert _calls(launchctl) == []
    assert not _plist(home).exists()


def test_the_agent_another_instance_wrote_is_left_alone(stack, tmp_path, repo, home, launchctl):
    """Same label, other data dir: that API belongs to the other instance."""
    _up(stack)
    other = _instance(tmp_path, repo, "other")
    _up(other)
    written = _plist(home).read_bytes()

    stack.down("core")
    stack.destroy("core")

    assert _plist(home).read_bytes() == written
    assert _calls(launchctl) == []
