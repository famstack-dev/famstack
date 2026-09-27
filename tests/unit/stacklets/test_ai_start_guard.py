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

import importlib.util
import sys
from pathlib import Path

import json

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

# Every stacklet has hooks with the same module names, so load this one
# from its path under its own name rather than by bare import.
_spec = importlib.util.spec_from_file_location(
    "ai_hooks_on_start",
    _REPO_ROOT / "stacklets" / "ai" / "hooks" / "on_start.py")
on_start = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(on_start)

_spec = importlib.util.spec_from_file_location(
    "ai_hooks_on_configure",
    _REPO_ROOT / "stacklets" / "ai" / "hooks" / "on_configure.py")
on_configure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(on_configure)


@pytest.fixture(autouse=True)
def state_dir(monkeypatch, tmp_path):
    """The hook's state markers live in the checkout. Every test here
    writes them somewhere else, or `stack down ai` on this Mac would
    start behaving differently."""
    monkeypatch.setattr(on_start, "STATE_DIR", tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    """oMLX's settings live in the home directory. Every test here gets
    its own, so none of them rewrites the engine on this Mac."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return tmp_path / "home"


class FakeCtx:
    """The hook context: config in, shell commands recorded rather than
    run, since they would start and stop services on this Mac."""

    def __init__(self, **cfg):
        self._cfg = cfg
        self.env: dict = {}
        self.shell_calls: list[str] = []

    def cfg(self, key, value=None, default=None):
        if value is not None:
            self._cfg[key] = value
            return value
        return self._cfg.get(key, default)

    def step(self, msg):
        pass

    def shell(self, cmd):
        self.shell_calls.append(cmd)


class NoEngineCtx(FakeCtx):
    """A Mac where Homebrew has no oMLX."""

    def shell(self, cmd):
        super().shell(cmd)
        if cmd == "brew list omlx":
            raise RuntimeError("Error: No such keg")

    def shell_live(self, cmd):
        self.shell_calls.append(cmd)


class TestManagedProviderNeedsItsEngine:

    def test_a_missing_engine_is_installed(self, monkeypatch):
        """After `stack ai switch managed` on a Mac with only the voice
        services, or a brew cleanup that took the binary: the stack
        manages the engine, so it installs it rather than refusing."""
        monkeypatch.setattr(on_start.shutil, "which",
                            lambda cmd: "/opt/homebrew/bin/brew" if cmd == "brew" else None)
        ctx = NoEngineCtx(provider="managed", openai_url="http://127.0.0.1:9/v1")

        on_start.run(ctx)

        assert "brew install omlx --with-grammar" in ctx.shell_calls

    def test_an_installed_engine_starts_normally(self, monkeypatch):
        monkeypatch.setattr(on_start.shutil, "which",
                            lambda _cmd: "/opt/homebrew/bin/omlx")
        on_start.run(FakeCtx(provider="managed"))  # must not raise



class TestExistingGuardsStillHold:

    def test_no_provider_configured_still_stops(self, monkeypatch):
        monkeypatch.setattr(on_start.shutil, "which", lambda _cmd: None)
        with pytest.raises(RuntimeError, match="provider not configured"):
            on_start.run(FakeCtx())



class TestTheLocalEngineIsStarted:
    """oMLX runs as a Homebrew service. Once it stopped, nothing started
    it again: `stack up ai` checked the configured address, found the
    service down or found another server there, and still reported the
    AI as running."""

    @pytest.fixture(autouse=True)
    def installed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(on_start.shutil, "which",
                            lambda _cmd: "/opt/homebrew/bin/omlx")

    def test_a_started_engine_is_stopped_again_by_stack_down_ai(self, state_dir):
        """`stack down ai` stops oMLX only when famstack manages it. An
        instance first set up with a remote endpoint never recorded that,
        so `down` left running what `up` had started."""
        on_start.run(FakeCtx(provider="managed", openai_url="http://127.0.0.1:9/v1"))

        assert (state_dir / "omlx-managed").exists()

    def test_a_stopped_engine_is_started(self):
        ctx = FakeCtx(provider="managed", openai_url="http://127.0.0.1:9/v1")

        on_start.run(ctx)

        assert ctx.shell_calls == ["brew services start omlx"]

    def test_a_running_engine_is_left_alone(self, httpserver):
        httpserver.expect_request("/v1/models").respond_with_json({"data": []})
        ctx = FakeCtx(provider="managed", openai_url=httpserver.url_for("/v1"))

        on_start.run(ctx)

        assert ctx.shell_calls == []



REMOTE = dict(provider="external", openai_url="https://ai.example.test/v1",
              openai_key="sk-test", whisper_url="https://stt.example.test/v1",
              whisper_key="stt-key", default="gpt-4.1-mini")


def _answer(monkeypatch, answer):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)


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


class TestInstallingOverAServerSetWithSwitch:
    """After `stack ai switch <url>`, installing the ai stacklet can mean
    two setups. Yes moves chat and voice onto the engine installed here.
    No keeps that server for chat and installs only the voice services,
    the setup of a Mac that serves its own AI app."""

    @pytest.fixture(autouse=True)
    def interactive_setup(self, monkeypatch):
        monkeypatch.delenv("STACK_SETUP_CONFIRMED", raising=False)

    def test_the_question_names_a_remote_server(self, monkeypatch, capsys):
        _answer(monkeypatch, "n")
        on_configure.run(FakeCtx(**REMOTE))

        assert "a remote AI server (https://ai.example.test/v1)" in capsys.readouterr().out

    def test_a_server_on_this_mac_is_not_called_remote(self, monkeypatch, capsys):
        _answer(monkeypatch, "n")
        on_configure.run(FakeCtx(**{**REMOTE, "openai_url": "http://localhost:8888/v1"}))

        out = capsys.readouterr().out
        assert "on this Mac that the stack does not manage (http://localhost:8888/v1)" in out
        assert "remote" not in out

    def test_no_keeps_the_server_and_goes_on_to_install_voice(self, monkeypatch):
        """The install that follows skips the engine for a server set
        with switch, and installs speech-to-text and text-to-speech."""
        _answer(monkeypatch, "n")
        ctx = FakeCtx(**REMOTE)

        on_configure.run(ctx)  # does not cancel

        assert ctx._cfg == REMOTE

    def test_no_is_the_default(self, monkeypatch):
        _answer(monkeypatch, "")
        ctx = FakeCtx(**REMOTE)

        on_configure.run(ctx)

        assert ctx._cfg == REMOTE

    def test_yes_switches_chat_and_voice_to_this_mac_together(self, monkeypatch):
        """Never one half remote and the other local."""
        _answer(monkeypatch, "y")
        ctx = FakeCtx(**REMOTE)

        on_configure.run(ctx)

        assert ctx._cfg["provider"] == "managed"
        assert ctx._cfg["openai_url"] == "http://localhost:42060/v1"
        assert ctx._cfg["whisper_url"] == "http://localhost:42062/v1"
        assert ctx._cfg["whisper_key"] == ""
        assert ctx._cfg["openai_key"] == "local"

    def test_without_a_terminal_nothing_is_installed(self, monkeypatch):
        """Nobody to answer: an agent or a script installing the stacklet
        must not change the family's AI setup."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        ctx = FakeCtx(**REMOTE)

        with pytest.raises(RuntimeError, match="terminal"):
            on_configure.run(ctx)

        assert ctx._cfg == REMOTE


class TestWhereOMLXListens:
    """Every service binds by the framework's rule: all interfaces in
    port mode, so the household's other machines reach it, loopback in
    domain mode. famstack never set oMLX's, so it kept its own default,
    loopback, and a second Mac pointed at this one was refused."""

    @pytest.fixture(autouse=True)
    def installed(self, monkeypatch, home):
        monkeypatch.setattr(on_start.shutil, "which",
                            lambda _cmd: "/opt/homebrew/bin/omlx")
        settings = home / ".omlx" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps(
            {"server": {"host": "127.0.0.1", "port": 42060},
             "auth": {"api_key": "local"}}))
        return settings

    def _ctx(self, url, bind):
        ctx = FakeCtx(provider="managed", openai_url=url)
        ctx.env["PORT_BIND_IP"] = bind
        return ctx

    def test_port_mode_listens_on_every_interface(self, installed):
        on_start.run(self._ctx("http://127.0.0.1:9/v1", "0.0.0.0"))

        settings = json.loads(installed.read_text())
        assert settings["server"] == {"host": "0.0.0.0", "port": 42060}
        assert settings["auth"] == {"api_key": "local"}

    def test_a_running_engine_is_restarted_to_listen_there(self, installed, httpserver):
        """oMLX reads its settings at start, so a running one keeps its
        old address until it restarts."""
        httpserver.expect_request("/v1/models").respond_with_json({"data": []})
        ctx = self._ctx(httpserver.url_for("/v1"), "0.0.0.0")

        on_start.run(ctx)

        assert ctx.shell_calls == ["brew services restart omlx"]

    def test_the_right_address_changes_nothing(self, installed, httpserver):
        httpserver.expect_request("/v1/models").respond_with_json({"data": []})
        before = installed.read_text()
        ctx = self._ctx(httpserver.url_for("/v1"), "127.0.0.1")

        on_start.run(ctx)

        assert installed.read_text() == before
        assert ctx.shell_calls == []
