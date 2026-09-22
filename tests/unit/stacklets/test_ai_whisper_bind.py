"""Where the local Whisper listens.

Every other service binds by the framework's rule: all interfaces in
port mode, so the household's other machines reach it, and loopback in
domain mode, where the reverse proxy is the way in. Whisper was pinned
to loopback in both, so a second Mac pointed at this one with
`stack ai connect <url> --whisper <this Mac>:42062` was refused, and a
proxy on the LAN side could not reach it either.

The LaunchAgent is written to a temporary home and launchd is not
asked to load it: the test reads what would be loaded.
"""

from __future__ import annotations

import importlib.util
import types
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "ai_hooks_on_install", _REPO_ROOT / "stacklets" / "ai" / "hooks" / "on_install.py")
on_install = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(on_install)


class FakeCtx:
    def __init__(self, env):
        self.env = env
        self.shell_calls: list[str] = []
        self.stack = types.SimpleNamespace(_cfg=lambda _s, _k, default="": "en")

    def cfg(self, key, value=None, default=""):
        return default

    def step(self, msg):
        pass

    def shell(self, cmd):
        self.shell_calls.append(cmd)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return tmp_path / "home"


def _listening_on(home, tmp_path, env) -> str:
    data = tmp_path / "data"
    (data / "ai" / "logs").mkdir(parents=True)
    on_install._setup_whisper_launchd(
        FakeCtx(env), data, data / "whisper-server", data / "model.bin", tmp_path)
    plist = (home / "Library" / "LaunchAgents" / f"{on_install.PLIST_LABEL}.plist").read_text()
    after = plist.split("<string>--host</string>", 1)[1]
    return after.split("<string>", 1)[1].split("</string>", 1)[0]


def test_port_mode_listens_on_every_interface(home, tmp_path):
    assert _listening_on(home, tmp_path, {"PORT_BIND_IP": "0.0.0.0"}) == "0.0.0.0"


def test_domain_mode_listens_on_loopback(home, tmp_path):
    assert _listening_on(home, tmp_path, {"PORT_BIND_IP": "127.0.0.1"}) == "127.0.0.1"


def test_without_the_framework_value_it_stays_on_loopback(home, tmp_path):
    assert _listening_on(home, tmp_path, {}) == "127.0.0.1"
