"""Starting the AI stacklet when its engine is no longer on the Mac.

First-run setup installs oMLX and records that setup is done. The install
hook is gated on that marker, so it never runs again -- which is fine
until the binary disappears underneath it. A Python upgrade that breaks
its virtualenv, a brew cleanup, a migrated machine: all leave a stacklet
that reports itself set up and starts its containers happily, with a
failing LLM health check as the only clue.

`stack up ai` installs the engine again when the stack manages it, and
otherwise leaves the AI server the admin chose alone.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import json

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

# A hook is a module of its stacklet's package, so the same hook name in
# two stacklets is two modules.
from stacklets.ai.hooks import on_start  # noqa: E402

# on_start asks Docker for the old speech container; here there is none.
pytestmark = pytest.mark.usefixtures("docker_with_no_containers")


@pytest.fixture(autouse=True)
def state_dir(monkeypatch, tmp_path):
    """The hook's state markers live in the checkout. Every test here
    writes them somewhere else, or `stack down ai` on this Mac would
    start behaving differently."""
    monkeypatch.setattr(on_start, "STATE_DIR", tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    """The LaunchAgents and the oMLX app's folder live in the home
    directory. Every test here gets its own, so none of them changes the
    engine on this Mac. famstack's oMLX folder is in the instance's data
    dir, `FakeCtx.stack.data`."""
    import types
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(FakeCtx, "stack", types.SimpleNamespace(data=tmp_path / "data", config={}),
                        raising=False)
    return tmp_path / "home"


def famstacks_settings(tmp_path) -> Path:
    """famstack's own oMLX settings, in the instance's data dir."""
    return tmp_path / "data" / "ai" / "omlx" / "settings.json"


@pytest.fixture(autouse=True)
def a_mac_with_memory_to_spare(monkeypatch):
    """The hook's context has no stack.toml, so the memory profile comes
    from this Mac's memory. On a 16 GB Mac the small profile fits oMLX's
    settings and its running server, which these tests do not test; every
    test here runs as on a Mac with the large profile."""
    monkeypatch.setattr("stack.profile.memory_gb", lambda: 64.0)


class FakeCtx:
    """The hook context: config in, shell commands recorded rather than
    run, since they would start and stop services on this Mac. `loaded`
    is whether launchd has famstack's oMLX: `launchctl list` fails when
    it has not."""

    def __init__(self, loaded: bool = True, **cfg):
        self._cfg = cfg
        self.env: dict = {}
        self.shell_calls: list[str] = []
        self.loaded = loaded

    def cfg(self, key, value=None, default=None):
        if value is not None:
            self._cfg[key] = value
            return value
        return self._cfg.get(key, default)

    def step(self, msg):
        pass

    def shell(self, cmd):
        self.shell_calls.append(cmd)
        if cmd.startswith("launchctl list") and not self.loaded:
            raise RuntimeError(cmd)

    def started(self) -> list[str]:
        """What it did to services, without asking launchd what it has."""
        return [c for c in self.shell_calls if not c.startswith("launchctl list")]


class NoEngineCtx(FakeCtx):
    """A Mac where Homebrew has no oMLX (`homebrew_omlx`, stood in by the test)."""

    def shell_live(self, cmd):
        self.shell_calls.append(cmd)


@pytest.fixture
def engine_not_answering(monkeypatch):
    """The engine as the start hook's probe sees it right after an install:
    not answering yet.

    The install writes the managed engine's fixed address,
    `localhost:42060`, over `[ai] openai_url`, so a stand-in server cannot
    be handed to the hook, and none can listen there: on a Mac running the
    test rig that port is the rig's own engine. The stand-in replaces the
    probe instead, the hook's one call over the network.
    """
    probe = importlib.import_module("stack.ai.probe")
    monkeypatch.setattr(probe, "probe",
                        lambda url, key="", **_: probe.ProbeResult(reachable=False))


class TestManagedProviderNeedsItsEngine:

    def test_a_missing_engine_is_installed(self, monkeypatch, engine_not_answering):
        """After `stack ai switch managed` on a Mac with only the voice
        services, or a brew cleanup that took the binary: the stack
        manages the engine, so it installs it rather than refusing."""
        monkeypatch.setattr(on_start.shutil, "which",
                            lambda cmd: "/opt/homebrew/bin/brew" if cmd == "brew" else None)
        monkeypatch.setattr(importlib.import_module("stacklets.ai.homebrew_omlx"),
                            "LIST_COMMAND", ("false",))
        ctx = NoEngineCtx(provider="managed", openai_url="http://127.0.0.1:9/v1")

        on_start.run(ctx)

        assert any(c.endswith("brew install omlx --with-grammar") for c in ctx.shell_calls)

    def test_an_installed_engine_starts_normally(self, monkeypatch, httpserver):
        """An engine that is installed and answering at the address in
        `[ai] openai_url` is neither installed again nor restarted."""
        monkeypatch.setattr(on_start.shutil, "which",
                            lambda _cmd: "/opt/homebrew/bin/omlx")
        httpserver.expect_request("/v1/models").respond_with_json(
            {"data": [{"id": "qwen3"}]})
        ctx = FakeCtx(provider="managed", openai_url=httpserver.url_for("/v1"))

        on_start.run(ctx)  # must not raise

        assert not any("brew install" in c or "brew services" in c
                       for c in ctx.shell_calls)



class TestExistingGuardsStillHold:

    def test_no_provider_configured_still_stops(self, monkeypatch):
        monkeypatch.setattr(on_start.shutil, "which", lambda _cmd: None)
        with pytest.raises(RuntimeError, match="provider not configured"):
            on_start.run(FakeCtx())



class TestTheLocalEngineIsStarted:
    """famstack's oMLX runs under its own LaunchAgent, in its own folder.
    Once it stopped, nothing started it again: `stack up ai` checked the
    configured address, found it down or found another server there, and
    still reported the AI as running. Now launchd loads it on every start
    that finds it unloaded."""

    @pytest.fixture(autouse=True)
    def installed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(on_start.shutil, "which",
                            lambda _cmd: "/opt/homebrew/bin/omlx")

    def test_a_started_engine_is_stopped_again_by_stack_down_ai(self, home):
        """`stack down ai` unloads famstack's LaunchAgent, which the start
        writes, also on an instance first set up with a remote endpoint."""
        on_start.run(FakeCtx(loaded=False, provider="managed", openai_url="http://127.0.0.1:9/v1"))

        assert (home / "Library" / "LaunchAgents" / "dev.famstack.omlx.plist").exists()

    def test_stack_down_ai_stays_down_across_a_login(self, home, tmp_path):
        """launchd loads every LaunchAgent at login, so `stack down ai`
        removes famstack's agent after it stops oMLX, and the next `stack
        up ai` writes and loads it again."""
        import os
        import subprocess

        on_start.run(FakeCtx(loaded=False, provider="managed", openai_url="http://127.0.0.1:9/v1"))
        agent = home / "Library" / "LaunchAgents" / "dev.famstack.omlx.plist"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        for tool in ("launchctl", "brew"):
            (bin_dir / tool).write_text(f'#!/bin/sh\necho "{tool} $*" >> "{tmp_path}/calls"\n')
            (bin_dir / tool).chmod(0o755)
        on_stop = Path(on_start.__file__).parent / "on_stop.sh"

        subprocess.run(["bash", str(on_stop)], check=True, capture_output=True,
                       env={**os.environ, "HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin"})

        assert f"launchctl unload {agent}" in (tmp_path / "calls").read_text()
        assert not agent.exists()
        ctx = FakeCtx(loaded=False, provider="managed", openai_url="http://127.0.0.1:9/v1")
        on_start.run(ctx)
        assert agent.exists()
        assert ctx.started() == [f'launchctl load "{agent}"']

    def test_a_stopped_engine_is_started(self, home):
        ctx = FakeCtx(loaded=False, provider="managed", openai_url="http://127.0.0.1:9/v1")

        on_start.run(ctx)

        agent = home / "Library" / "LaunchAgents" / "dev.famstack.omlx.plist"
        assert ctx.started() == [f'launchctl load "{agent}"']

    def test_a_running_engine_is_left_alone(self, tmp_path):
        on_start.run(FakeCtx(loaded=False, provider="managed", openai_url="http://127.0.0.1:9/v1"))
        ctx = FakeCtx(provider="managed", openai_url="http://127.0.0.1:9/v1")

        on_start.run(ctx)

        assert ctx.started() == []

    def test_an_instance_on_homebrews_service_moves_over_with_its_key(self, state_dir, tmp_path):
        """An earlier release ran famstack's oMLX as a Homebrew service with
        `~/.omlx` (the marker says famstack started it). Its first start
        here stops that service once and keeps the key `[ai]` sends, so the
        apps that use it keep working."""
        (state_dir / "omlx-managed").touch()
        ctx = FakeCtx(loaded=False, provider="managed", openai_url="http://127.0.0.1:9/v1",
                      openai_key="kq7m-2xpt-r9fd-h3wa")

        on_start.run(ctx)

        assert ctx.started()[0] == "brew services stop omlx"
        assert ctx.started()[-1].startswith("launchctl load")
        assert not (state_dir / "omlx-managed").exists()
        settings = json.loads(famstacks_settings(tmp_path).read_text())
        assert settings["auth"]["api_key"] == "kq7m-2xpt-r9fd-h3wa"


REMOTE = dict(provider="external", openai_url="https://ai.example.test/v1",
              openai_key="sk-test", whisper_url="https://stt.example.test/v1",
              whisper_key="stt-key", default="gpt-4.1-mini")


class TestAnInstalledStackletKeepsItsEngine:
    """Chat on an AI server elsewhere with voice on this Mac is a setup an
    admin chooses with `stack ai switch`. Starting the installed stacklet
    starts what it runs and leaves that choice alone: a production Mac
    serving oMLX as an app, with the stacklet for speech, was asked on
    every `stack up ai` to hand its engine over."""

    @pytest.fixture(autouse=True)
    def installed(self, monkeypatch):
        monkeypatch.setattr(on_start.shutil, "which",
                            lambda _cmd: "/opt/homebrew/bin/omlx")

    def test_it_starts_without_asking(self, monkeypatch):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", _no_question_expected)
        ctx = FakeCtx(**REMOTE)

        on_start.run(ctx)

        assert ctx._cfg == REMOTE
        assert ctx.shell_calls == []

    def test_it_starts_without_a_terminal(self, monkeypatch):
        """An agent or a script restarting the stacklet is not stopped."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        ctx = FakeCtx(**REMOTE)

        on_start.run(ctx)

        assert ctx._cfg == REMOTE

    def test_it_names_the_command_that_switches_the_engine(self, capsys):
        on_start.run(FakeCtx(**REMOTE))

        out = capsys.readouterr().out
        assert "https://ai.example.test/v1" in out
        assert "stack ai switch managed" in out


def _no_question_expected(prompt):
    raise AssertionError(f"asked {prompt!r}")


class TestWhereOMLXListens:
    """Every service binds by the framework's rule: all interfaces in
    port mode, so the household's other machines reach it, loopback in
    domain mode. famstack never set oMLX's, so it kept its own default,
    loopback, and a second Mac pointed at this one was refused."""

    @pytest.fixture(autouse=True)
    def installed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(on_start.shutil, "which",
                            lambda _cmd: "/opt/homebrew/bin/omlx")
        settings = famstacks_settings(tmp_path)
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps(
            {"server": {"host": "127.0.0.1", "port": 42060},
             "model": {"model_dir": str(tmp_path / "home" / ".omlx" / "models")},
             "auth": {"api_key": "local"}}))
        return settings

    def _ctx(self, bind):
        ctx = FakeCtx(provider="managed", openai_url="http://127.0.0.1:9/v1")
        ctx.env["PORT_BIND_IP"] = bind
        return ctx

    def test_port_mode_listens_on_every_interface(self, installed):
        on_start.run(self._ctx("0.0.0.0"))

        settings = json.loads(installed.read_text())
        assert settings["server"] == {"host": "0.0.0.0", "port": 42060}
        assert settings["auth"] == {"api_key": "local"}

    def test_a_running_engine_is_restarted_to_listen_there(self, installed):
        """oMLX reads its settings at start, so a running one keeps its
        old address until it restarts."""
        on_start.run(self._ctx("127.0.0.1"))
        ctx = self._ctx("0.0.0.0")

        on_start.run(ctx)

        assert [c.split()[1] for c in ctx.started()] == ["unload", "load"]

    def test_the_right_address_changes_nothing(self, installed):
        on_start.run(self._ctx("127.0.0.1"))
        before = installed.read_text()
        ctx = self._ctx("127.0.0.1")

        on_start.run(ctx)

        assert installed.read_text() == before
        assert ctx.started() == []
