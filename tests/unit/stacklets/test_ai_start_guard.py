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
import tomllib

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

# A hook is a module of its stacklet's package, so the same hook name in
# two stacklets is two modules.
from stack import launchd  # noqa: E402
from stacklets.ai import homebrew_service, omlx_start  # noqa: E402
from stacklets.ai.hooks import on_start  # noqa: E402
from tests.unit.stacklets.launchd_stand_in import LAUNCHCTL  # noqa: E402
from tests.unit.stacklets.ps_stand_in import macos_ps  # noqa: E402

# on_start asks Docker for the old speech container; here there is none.
pytestmark = pytest.mark.usefixtures("docker_with_no_containers")


@pytest.fixture(autouse=True)
def state_dir(monkeypatch, tmp_path):
    """The stacklet's markers live in the checkout. Every test here
    writes them somewhere else, or `stack down ai` on this Mac would
    start behaving differently."""
    monkeypatch.setattr(homebrew_service, "STATE_DIR", tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    """The LaunchAgents and the oMLX app's folder live in the home
    directory. Every test here gets its own, so none of them changes the
    engine on this Mac. famstack's oMLX folder is in the instance's data
    dir, `FakeCtx.stack.data`. `Path.home()` reads `HOME`, as every
    program the tests start does."""
    import types
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(FakeCtx, "stack", types.SimpleNamespace(data=tmp_path / "data", config={}),
                        raising=False)
    return tmp_path / "home"


def homebrew_has_omlx(monkeypatch, has: bool) -> None:
    """Homebrew lists the oMLX formula, or does not, whatever the Mac that
    runs the tests has: `true` or `false` answers in place of `brew list`."""
    homebrew_omlx = importlib.import_module("stacklets.ai.homebrew_omlx")
    monkeypatch.setattr(homebrew_omlx, "LIST_COMMAND", ("true",) if has else ("false",))


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
    it has not, a remove or an unload drops it, a load adds it."""

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

    def shell(self, cmd, timeout=None):
        self.shell_calls.append(cmd)
        if cmd.startswith(("launchctl remove", "launchctl unload")):
            self.loaded = False
        elif cmd.startswith("launchctl load"):
            self.loaded = True
        if cmd.startswith("launchctl list"):
            # launchd's "no such job", as the list command says it
            # (`launchd.listed`).
            return '{ "PID" = 4242; };' if self.loaded else launchd.NOT_LISTED
        return ""

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

    def test_a_missing_engine_is_left_to_the_preparation(self, monkeypatch, engine_not_answering,
                                                         httpserver):
        """After a brew cleanup that took the binary, the start installs
        nothing: every `stack up ai` prepares first, and the preparation
        installs oMLX (`prepare.py`)."""
        monkeypatch.setattr("shutil.which",
                            lambda cmd: "/opt/homebrew/bin/brew" if cmd == "brew" else None)
        homebrew_has_omlx(monkeypatch, False)
        httpserver.expect_request("/v1/models").respond_with_json({"data": []})
        ctx = NoEngineCtx(provider="managed", openai_url=httpserver.url_for("/v1"))

        on_start.run(ctx)

        assert not any("brew install" in c for c in ctx.shell_calls)

    def test_an_installed_engine_starts_normally(self, monkeypatch, httpserver):
        """An engine that is installed and answering at the address in
        `[ai] openai_url` is neither installed again nor restarted."""
        homebrew_has_omlx(monkeypatch, True)
        httpserver.expect_request("/v1/models").respond_with_json(
            {"data": [{"id": "qwen3"}]})
        ctx = FakeCtx(provider="managed", openai_url=httpserver.url_for("/v1"))

        on_start.run(ctx)  # must not raise

        assert not any("brew install" in c or "brew services" in c
                       for c in ctx.shell_calls)



class TestExistingGuardsStillHold:

    def test_no_provider_configured_still_stops(self, monkeypatch):
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
        homebrew_has_omlx(monkeypatch, True)

    def test_a_started_engine_is_stopped_again_by_stack_down_ai(self, home, state_dir,
                                                                monkeypatch):
        """`stack down ai` stops famstack's oMLX, which the start loaded,
        and removes its agent, also on an instance first set up with a
        remote endpoint."""
        from stacklets.ai.hooks import on_stop

        on_start.run(FakeCtx(loaded=False, provider="managed", openai_url="http://127.0.0.1:9/v1"))
        agent = home / "Library" / "LaunchAgents" / "dev.famstack.omlx.plist"
        assert agent.exists()
        ctx = FakeCtx(loaded=True)

        on_stop.run(ctx)

        assert "launchctl remove dev.famstack.omlx" in ctx.started()
        assert not agent.exists()

    def test_a_stopped_engine_is_started(self, home):
        ctx = FakeCtx(loaded=False, provider="managed", openai_url="http://127.0.0.1:9/v1")

        on_start.run(ctx)

        agent = home / "Library" / "LaunchAgents" / "dev.famstack.omlx.plist"
        assert ctx.started() == [f"launchctl load {agent} 2>&1"]

    def test_a_running_engine_is_left_alone(self, tmp_path):
        on_start.run(FakeCtx(loaded=False, provider="managed", openai_url="http://127.0.0.1:9/v1"))
        ctx = FakeCtx(provider="managed", openai_url="http://127.0.0.1:9/v1")

        on_start.run(ctx)

        assert ctx.started() == []

    def test_an_instance_on_homebrews_service_moves_over_with_its_key(self, state_dir, tmp_path,
                                                                     httpserver):
        """An earlier release ran famstack's oMLX as a Homebrew service with
        `~/.omlx` (the marker says famstack started it). Its first
        preparation here stops that service once and keeps the key `[ai]` sends, so the
        apps that use it keep working."""
        (state_dir / "omlx-managed").touch()
        # famstack's oMLX answers once it started: the move waits for that.
        httpserver.expect_request("/v1/models").respond_with_json({"data": []})
        ctx = FakeCtx(loaded=False, provider="managed", openai_url=httpserver.url_for("/v1"),
                      openai_key="kq7m-2xpt-r9fd-h3wa")

        homebrew_service.move(ctx)

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
        homebrew_has_omlx(monkeypatch, True)

    def test_it_starts_without_asking(self, terminal, monkeypatch, capsys):
        terminal.types("y")
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        ctx = FakeCtx(**REMOTE)

        on_start.run(ctx)

        assert ctx._cfg == REMOTE
        assert ctx.shell_calls == []
        assert sys.stdin.tell() == 0, "the typed answer was read, so a question was asked"

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


class TestWhereOMLXListens:
    """Every service binds by the framework's rule: all interfaces in
    port mode, so the household's other machines reach it, loopback in
    domain mode. famstack never set oMLX's, so it kept its own default,
    loopback, and a second Mac pointed at this one was refused."""

    @pytest.fixture(autouse=True)
    def installed(self, monkeypatch, tmp_path):
        homebrew_has_omlx(monkeypatch, True)
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

    def test_a_running_engine_is_restarted_to_listen_there(self, installed):
        """oMLX reads its settings at start, so a running one keeps its
        old address until it restarts."""
        on_start.run(self._ctx("127.0.0.1"))
        ctx = self._ctx("0.0.0.0")

        on_start.run(ctx)

        assert [c.split()[1] for c in ctx.started()] == ["kickstart"]

    def test_the_right_address_changes_nothing(self, installed):
        on_start.run(self._ctx("127.0.0.1"))
        before = installed.read_text()
        ctx = self._ctx("127.0.0.1")

        on_start.run(ctx)

        assert installed.read_text() == before
        assert ctx.started() == []


# ── One lifecycle for famstack's oMLX and Whisper ────────────────────────

class TestTheHostServicesShareOneLifecycle:
    """famstack's oMLX and Whisper run under LaunchAgents, and both follow
    one lifecycle: `stack up ai` starts both, `stack down ai` stops both,
    and what is down stays down when the Mac restarts and the admin logs
    in, because launchd then loads every agent it finds.

    The hooks run as the framework runs them, through the hook resolver
    with a real hook context on a throwaway instance. The Mac is stood in:
    a home of the test's own; Homebrew with oMLX 0.8.0rc1 and its
    program; a built Whisper and its model in the data dir; `launchctl`
    as a launchd that remembers its jobs; `curl` that finds both servers
    answering. Speech is on."""

    @pytest.fixture
    def mac(self, tmp_path, monkeypatch, _stand_in_folder, httpserver):
        import types

        from stack import Stack
        from stack.hooks import HookResolver, build_hook_ctx
        from stack.output import CollectorOutput

        home, bin_dir, jobs = tmp_path / "home", tmp_path / "bin", tmp_path / "launchd"
        calls = tmp_path / "calls"
        for folder in (home, bin_dir, jobs):
            folder.mkdir(parents=True)
        calls.write_text("")
        program = tmp_path / "opt" / "omlx" / "bin" / "omlx"
        program.parent.mkdir(parents=True)
        program.write_text("#!/bin/sh\nexit 0\n")
        stand_ins = {
            # Homebrew's service is the job `homebrew.mxcl.omlx` in the
            # launchd stand-in; `$BREW_STOP_FAILS` keeps it running.
            "brew": f'echo "brew $*" >> "{calls}"\n'
                    'if [ "$1 $2" = "list --versions" ]; then echo "omlx 0.8.0rc1"; fi\n'
                    'if [ "$1 $2" = "services stop" ]; then\n'
                    '  [ -n "$BREW_STOP_FAILS" ] && exit 1\n'
                    f'  rm -f "{jobs}/homebrew.mxcl.omlx"; fi\n'
                    f'if [ "$1 $2" = "services start" ]; then touch "{jobs}/homebrew.mxcl.omlx"; fi',
            "curl": "exit 0",
        }
        for name, body in stand_ins.items():
            (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
        (bin_dir / "launchctl").write_text(LAUNCHCTL)  # a launchd with a memory
        for tool in bin_dir.iterdir():
            tool.chmod(0o755)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("CALLS", str(calls))
        monkeypatch.setenv("LAUNCHD_JOBS", str(jobs))
        monkeypatch.setenv("PATH", f"{bin_dir}:{_stand_in_folder}:/usr/bin:/bin")
        data = tmp_path / "data"
        whisper = data / "ai" / "whisper.cpp" / "build" / "bin" / "whisper-server"
        whisper.parent.mkdir(parents=True)
        whisper.write_text("")
        models = data / "ai" / "whisper-models"
        models.mkdir(parents=True)
        (models / "ggml-large-v3-turbo.bin").write_text("")
        (data / "ai" / "logs").mkdir(parents=True)
        (tmp_path / "stack.toml").write_text(
            f'[core]\ndata_dir = "{data}"\nprofile = "large"\n\n'
            f'[ai]\nprovider = "managed"\nopenai_url = "{httpserver.url_for("/v1")}"\n'
            'openai_key = "kq7m-2xpt-r9fd-h3wa"\nspeech = true\n')
        (tmp_path / ".stack").mkdir()
        stack = Stack(_REPO_ROOT, data, instance_dir=tmp_path, output=CollectorOutput())
        hooks = HookResolver(Path(on_start.__file__).resolve().parent.parent)
        state = tmp_path / "state"

        def ctx():
            return build_hook_ctx("ai", env=stack.env("ai"), step_fn=stack.output.step,
                                  stack=stack)

        def hook(name):
            if not hooks.run(name, ctx()):
                raise RuntimeError(f"{name} failed: {stack.output.errors}")

        class Mac:
            agents = home / "Library" / "LaunchAgents"
            folder = data / "ai" / "omlx"
            omlx = home / ".omlx"
            state_dir = state
            output = stack.output

            @staticmethod
            def up():
                hook("on_start")

            @staticmethod
            def start(**env):
                """`on_start` itself, so a refusal surfaces as it is raised."""
                for name, value in env.items():
                    monkeypatch.setenv(name, value)
                on_start.run(ctx())

            @staticmethod
            def start_after_update(**env):
                """`stack up ai` after an update from 0.4.x: the move, which
                the preparation runs, then `on_start`."""
                for name, value in env.items():
                    monkeypatch.setenv(name, value)
                if homebrew_service.was_famstacks():
                    homebrew_service.move(ctx())
                on_start.run(ctx())

            @staticmethod
            def down():
                hook("on_stop")

            @staticmethod
            def old_service_runs():
                (jobs / "homebrew.mxcl.omlx").touch()

            @staticmethod
            def login():
                """The admin logs in: launchd loads every agent it finds."""
                for agent in (home / "Library" / "LaunchAgents").glob("*.plist"):
                    (jobs / agent.stem).touch()

            @staticmethod
            def running() -> set[str]:
                return {job.name for job in jobs.iterdir()}

            @staticmethod
            def calls() -> list[str]:
                return calls.read_text().splitlines()

        monkeypatch.setattr(homebrew_service, "STATE_DIR", state)
        state.mkdir()
        return types.SimpleNamespace(**{k: v for k, v in vars(Mac).items()
                                        if not k.startswith("__")})

    def test_stack_up_ai_starts_omlx_and_whisper(self, mac):
        mac.up()

        assert mac.running() == {"dev.famstack.omlx", "dev.famstack.whisper"}

    def test_stack_down_ai_keeps_both_down_across_a_login(self, mac):
        """Given `stack down ai`, when the Mac restarts and the admin logs
        in, then neither famstack's oMLX nor Whisper starts."""
        mac.up()

        mac.down()
        mac.login()

        assert mac.running() == set()
        assert list(mac.agents.glob("*.plist")) == []

    def test_stack_up_ai_after_a_down_starts_both_again(self, mac):
        mac.up()
        mac.down()

        mac.up()

        assert mac.running() == {"dev.famstack.omlx", "dev.famstack.whisper"}

    def test_a_second_start_leaves_both_running(self, mac):
        mac.up()
        before = mac.calls()

        mac.up()

        assert not [c for c in mac.calls()[len(before):] if " list " not in c]

    def test_a_failed_start_keeps_the_omlx_that_ran_before(self, mac, monkeypatch):
        """Given a running AI and a later start that fails halfway, then the
        AI that ran before keeps running: the run takes back only what it
        started."""
        mac.up()

        def disk_full(*_args, **_kwargs):
            raise OSError("No space left on device")

        monkeypatch.setattr(omlx_start, "fit_settings_file", disk_full)
        with pytest.raises(OSError):
            mac.start()

        assert "dev.famstack.omlx" in mac.running()
        assert (mac.agents / "dev.famstack.omlx.plist").exists()

    def test_the_settings_file_with_the_key_is_for_the_admin_only(self, mac):
        """The file holds the key every app of the family sends."""
        mac.up()

        assert (mac.folder / "settings.json").stat().st_mode & 0o777 == 0o600

    def test_a_settings_file_an_earlier_start_left_readable_is_closed(self, mac):
        settings = mac.folder / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text('{"auth": {"api_key": "kq7m-2xpt-r9fd-h3wa"}}')
        settings.chmod(0o644)

        mac.up()

        assert settings.stat().st_mode & 0o777 == 0o600

    def test_a_restart_that_fails_leaves_the_ai_that_ran(self, mac, monkeypatch):
        """Given a running oMLX whose settings changed, and a launchd that
        then cannot load the agent, then oMLX stays loaded: a restart for
        new settings never takes it out of launchd."""
        mac.up()
        monkeypatch.setattr(omlx_start, "fit_settings_file", lambda *_args, **_kwargs: True)

        mac.start(LOAD_FAILS="1")

        assert "dev.famstack.omlx" in mac.running()

    # ── An unreadable settings file ──────────────────────────────────────

    def test_an_unreadable_settings_file_stops_the_start_with_its_name(self, mac):
        """Given famstack's oMLX settings file that is not JSON, then the
        start says which file and stops, and changes nothing."""
        settings = mac.folder / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text('{"server": {"port": 42060,')

        with pytest.raises(RuntimeError) as refused:
            mac.start()

        assert str(settings) in str(refused.value)
        assert settings.read_text() == '{"server": {"port": 42060,'
        # Asking Homebrew and launchd what they have changes nothing.
        assert [c for c in mac.calls() if not c.startswith(("brew list", "launchctl list"))] == []
        assert mac.running() == set()

    # ── The update from 0.4.4 ────────────────────────────────────────────

    OLD_MODEL_SETTINGS = {"models": {"Qwen3.5-9B-MLX-4bit": {
        "chat_template_kwargs": {"enable_thinking": False}, "max_context_window": 32768}}}

    @pytest.fixture
    def from_0_4_4(self, mac):
        """An instance set up by 0.4.4: famstack ran oMLX as Homebrew's
        service in `~/.omlx`, with famstack's port, key and model settings
        there, and the marker that says so."""
        mac.omlx.mkdir()
        (mac.omlx / "settings.json").write_text(json.dumps(
            {"server": {"port": 42060}, "auth": {"api_key": "kq7m-2xpt-r9fd-h3wa"}}))
        (mac.omlx / "model_settings.json").write_text(json.dumps(self.OLD_MODEL_SETTINGS))
        (mac.state_dir / "omlx-managed").touch()
        mac.old_service_runs()
        return mac

    @pytest.fixture
    def answers(self, httpserver):
        """famstack's oMLX answers at `[ai] openai_url` with `[ai]`'s key
        once it started (the stand-in plays it)."""
        httpserver.expect_request(
            "/v1/models", headers={"Authorization": "Bearer kq7m-2xpt-r9fd-h3wa"},
        ).respond_with_json({"data": [{"id": "Qwen3.5-9B-MLX-4bit"}]})

    def test_the_first_start_carries_the_old_model_settings(self, from_0_4_4, answers):
        mac = from_0_4_4

        mac.start_after_update()

        carried = json.loads((mac.folder / "model_settings.json").read_text())
        assert carried == self.OLD_MODEL_SETTINGS
        assert "brew services stop omlx" in mac.calls()
        assert mac.running() == {"dev.famstack.omlx", "dev.famstack.whisper"}

    def test_without_a_key_in_stack_toml_it_asks_with_the_key_omlx_took(
            self, from_0_4_4, httpserver, tmp_path):
        """An instance without `[ai] openai_key`: its oMLX gets a key of
        its own (`omlx.own_key`), and the move asks it with that, never
        with an empty key, which oMLX refuses."""
        mac = from_0_4_4
        toml = tmp_path / "stack.toml"
        toml.write_text(toml.read_text().replace('openai_key = "kq7m-2xpt-r9fd-h3wa"\n', ""))
        httpserver.expect_request("/v1/models").respond_with_json({"data": []})

        mac.start_after_update()

        key = json.loads((mac.folder / "settings.json").read_text())["auth"]["api_key"]
        assert key not in ("", "local")
        asked = {r.headers.get("Authorization") for r, _ in httpserver.log}
        assert asked == {f"Bearer {key}"}
        assert not (mac.state_dir / "omlx-managed").exists()

    def test_an_update_from_the_shared_key_keeps_it_everywhere(
            self, from_0_4_4, httpserver, tmp_path):
        """Given an instance whose oMLX used the shared key `local`, when
        the first start after the update moves it, then famstack's oMLX
        and `[ai]` keep `local`: apps on other computers that send it go
        on working. `stack ai key new` is how the admin replaces it."""
        mac = from_0_4_4
        (mac.omlx / "settings.json").write_text(json.dumps(
            {"server": {"port": 42060}, "auth": {"api_key": "local"}}))
        toml = tmp_path / "stack.toml"
        toml.write_text(toml.read_text().replace('"kq7m-2xpt-r9fd-h3wa"', '"local"'))
        httpserver.expect_request(
            "/v1/models", headers={"Authorization": "Bearer local"},
        ).respond_with_json({"data": []})

        mac.start_after_update()

        settings = json.loads((mac.folder / "settings.json").read_text())
        assert settings["auth"]["api_key"] == "local"
        assert tomllib.loads(toml.read_text())["ai"]["openai_key"] == "local"
        assert mac.running() == {"dev.famstack.omlx", "dev.famstack.whisper"}

    def test_the_model_settings_are_carried_once(self, from_0_4_4, answers):
        """A later change in `~/.omlx` is the oMLX app's, never famstack's."""
        mac = from_0_4_4
        mac.start_after_update()
        (mac.omlx / "model_settings.json").write_text('{"models": {}}')

        mac.start_after_update()

        carried = json.loads((mac.folder / "model_settings.json").read_text())
        assert carried == self.OLD_MODEL_SETTINGS

    def test_it_says_the_apps_settings_still_name_famstacks_port(self, from_0_4_4, answers, capsys):
        mac = from_0_4_4

        mac.start_after_update()

        said = capsys.readouterr().out + " ".join(mac.output.steps) + " ".join(
            mac.output.warnings)
        assert str(mac.omlx / "settings.json") in said
        assert "42060" in said and "8000" in said

    def test_the_old_service_stays_the_fallback_until_famstacks_omlx_answers(
            self, from_0_4_4, monkeypatch):
        """Given famstack's oMLX that launchd loaded but that never answers,
        then the move is not done: the old service runs again, its marker
        stays, and famstack's agent goes."""
        mac = from_0_4_4
        monkeypatch.setattr(homebrew_service, "MOVE_READY_SECONDS", 1)

        with pytest.raises(RuntimeError, match="did not answer"):
            mac.start_after_update()

        calls = mac.calls()
        assert calls.index("brew services start omlx") > calls.index("brew services stop omlx")
        assert (mac.state_dir / "omlx-managed").exists()
        assert "dev.famstack.omlx" not in mac.running()
        # Port 42060 is free for the old service only once famstack's oMLX
        # has exited: launchd is asked after the stop, before the start.
        removed = calls.index("launchctl remove dev.famstack.omlx")
        restarted = calls.index("brew services start omlx")
        assert "launchctl list dev.famstack.omlx" in calls[removed:restarted]

    def test_a_ctrl_c_right_after_the_old_service_stopped_runs_it_again(
            self, from_0_4_4, monkeypatch):
        """Ctrl-C while the stop of 0.4's service still asks launchd."""
        import os

        from stacklets.ai import homebrew_service
        mac = from_0_4_4

        def stopped_then_interrupted(_run):
            Path(os.environ["LAUNCHD_JOBS"], "homebrew.mxcl.omlx").unlink()
            raise KeyboardInterrupt

        monkeypatch.setattr(homebrew_service, "stop", stopped_then_interrupted)
        with pytest.raises(KeyboardInterrupt):
            mac.start_after_update()

        assert "homebrew.mxcl.omlx" in mac.running()

    def test_an_old_service_that_does_not_stop_keeps_running_alone(self, from_0_4_4):
        """Given Homebrew's service still running after its stop, then
        famstack's oMLX does not start beside it on port 42060, where the
        old one would answer in its place."""
        mac = from_0_4_4

        with pytest.raises(RuntimeError, match="Homebrew"):
            mac.start_after_update(BREW_STOP_FAILS="1")

        assert mac.running() == {"homebrew.mxcl.omlx"}
        assert (mac.state_dir / "omlx-managed").exists()

    def test_a_ctrl_c_during_the_move_runs_the_old_service_again(self, from_0_4_4, monkeypatch):
        mac = from_0_4_4
        from stacklets.ai import omlx_service

        def interrupted(*_args, **_kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(omlx_service, "start", interrupted)
        with pytest.raises(KeyboardInterrupt):
            mac.start_after_update()

        assert "brew services start omlx" in mac.calls()
        assert (mac.state_dir / "omlx-managed").exists()

    def test_an_unreadable_settings_file_stops_the_move_before_it_changes_anything(
            self, from_0_4_4):
        """Given famstack's settings file that is not JSON, then the move
        refuses with the file's name, and Homebrew's service keeps running
        untouched."""
        from stacklets.ai import omlx_service
        mac = from_0_4_4
        (mac.folder / "settings.json").parent.mkdir(parents=True, exist_ok=True)
        (mac.folder / "settings.json").write_text("{not json")

        with pytest.raises(omlx_service.UnreadableSettings):
            mac.start_after_update()

        assert not any(c.startswith("brew services") for c in mac.calls())
        assert mac.running() == {"homebrew.mxcl.omlx"}

    def test_a_failed_start_after_the_move_runs_the_old_service_again(self, from_0_4_4):
        """Given a managed run that fails halfway, then the previous AI
        keeps running: Homebrew's service starts again, the marker stays
        for the next try, and no famstack agent is left behind."""
        mac = from_0_4_4

        with pytest.raises(RuntimeError):
            mac.start_after_update(LOAD_FAILS="1")

        calls = mac.calls()
        assert calls.index("brew services start omlx") > calls.index("brew services stop omlx")
        assert (mac.state_dir / "omlx-managed").exists()
        assert not (mac.agents / "dev.famstack.omlx.plist").exists()


# ── At login, beside the oMLX app ────────────────────────────────────────

# The bundle of the tests' stand-in for the oMLX app, below a folder with
# a space in its name. Not `oMLX.app`, so the guard of a famstack instance
# on the same Mac does not see it.
STAND_IN_APP = "Shared Apps/Stand-in.app"

# How long a test waits for the guard before it calls the guard hung. It
# is a bound for a fault, never a measure of speed: the guard answers in
# well under a second at rest, and over 10 seconds in a loaded parallel run.
HANG = 120


def stand_in_app(tmp_path: Path) -> tuple[str, str]:
    """The stand-in's executable and the bundle the test looks for. The
    executable is a link to `sleep`: macOS names a process by the path it
    was started from, so `ps -axo comm=` shows the link's path. The link
    is named `sleep`, because the app check matches the bundle, not the
    name, and a multi-call `sleep` (Ubuntu 26.04's Rust coreutils) refuses
    to run under another name."""
    bundle = f"{tmp_path}/{STAND_IN_APP}/Contents/"
    app = Path(bundle) / "MacOS" / "sleep"
    app.parent.mkdir(parents=True, exist_ok=True)
    if not app.is_symlink():
        app.symlink_to("/bin/sleep")
    return str(app), bundle


class TestFamstacksOmlxStepsAsideForTheApp:
    """Two MLX servers on one graphics chip can crash the Mac. launchd
    starts famstack's oMLX at login without asking `stack up ai`, so the
    agent runs a guard instead of oMLX itself. Before every start it waits
    a moment (an app that is a login item may start after the agent),
    looks for the oMLX app once, and starts oMLX only when the app does
    not run. While oMLX runs it looks again every few seconds, and stops
    oMLX when the app appears. Then it exits 0, which launchd does not
    restart. So does an oMLX that cannot start at all. When oMLX ends by
    itself after it ran (the restart button of its admin page), the guard
    exits 1, and launchd starts it again.

    The guard runs here as launchd runs it: the script from the agent's
    arguments, with the agent's own PATH, and a `ps` that answers as
    macOS's does on any host (`ps_stand_in.py`; the real one on a Mac). The app is a
    process whose executable lies in a `Stand-in.app` bundle of the test's
    folder (`stand_in_app`), which the guard is told to look for: an oMLX
    app open on the Mac that runs the tests is not seen, and the guard of
    famstack's oMLX running on that Mac does not take the stand-in for the
    app. oMLX is a stand-in that writes down that it ran
    and that it got TERM."""

    @pytest.fixture
    def guard(self, tmp_path, monkeypatch):
        import plistlib
        import shlex
        import subprocess

        from stacklets.ai import omlx_service

        from stacklets.ai import omlx_app

        app, bundle = stand_in_app(tmp_path)
        check = omlx_app.app_check(bundle, ps=macos_ps(tmp_path))
        ran, termed, log = tmp_path / "omlx-ran", tmp_path / "omlx-termed", tmp_path / "omlx.log"
        apps: list = []
        processes: list = []

        class Guard:
            def runs(self, body="/bin/sleep 30 & wait", **timing):
                """The guard as launchd starts it. `body` is the stand-in
                oMLX; None for a program that is not there."""
                program = tmp_path / "omlx"
                if body is not None:
                    # The trap comes first: a TERM sent once `ran` exists
                    # must find it set.
                    program.write_text(
                        f'#!/bin/sh\ntrap \'echo term >> "{termed}"; exit 0\' TERM\n'
                        f'echo "$*" >> "{ran}"\n{body}\n')
                    program.chmod(0o755)
                fast = {"settle": 0, "poll": 0.2, "startup": 0}
                agent = omlx_service.agent(
                    tmp_path / "data", str(program),
                    timing=omlx_service.GuardTiming(**{**fast, **timing}),
                    app=bundle, ps=macos_ps(tmp_path))
                launchd = plistlib.loads(agent.plist.encode())
                for path, text in agent.files.items():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text)
                    path.chmod(0o755)
                with log.open("w") as out:
                    started = subprocess.Popen(launchd["ProgramArguments"], stdout=out,
                                               stderr=subprocess.STDOUT,
                                               env=launchd["EnvironmentVariables"])
                processes.append(started)
                return started

            def app_starts(self, after: float = 0):
                apps.append(subprocess.Popen(
                    ["/bin/sh", "-c",
                     f"/bin/sleep {after}; exec {shlex.quote(app)} 30"]))

            def a_process_names_the_app(self):
                """A process that is not the app, with the app's path as an
                argument (an editor, a `tail` of a file in the bundle)."""
                apps.append(subprocess.Popen(["/bin/sh", "-c", "/bin/sleep 30", app]))

            def omlx_ran(self) -> bool:
                return ran.exists()

            def omlx_termed(self) -> bool:
                return termed.exists()

            def wait_until_omlx_runs(self):
                import time
                deadline = time.monotonic() + HANG
                while not ran.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert ran.exists(), self.log()

            def wait_until_the_app_runs(self):
                """Until the guard's own check would see the app."""
                import time
                deadline = time.monotonic() + HANG
                while subprocess.run(["/bin/sh", "-c", check]).returncode != 0:
                    assert time.monotonic() < deadline, "the stand-in app never showed"
                    time.sleep(0.05)

            def log(self) -> str:
                return log.read_text() if log.exists() else ""

        yield Guard()
        for process in apps + processes:
            process.kill()
            process.wait()

    def test_launchd_restarts_it_only_after_a_failure(self, tmp_path):
        """`KeepAlive` with `SuccessfulExit` false: an exit 0 stays down."""
        import plistlib

        from stacklets.ai import omlx_service

        agent = omlx_service.agent(tmp_path, "/opt/homebrew/opt/omlx/bin/omlx")

        assert plistlib.loads(agent.plist.encode())["KeepAlive"] == {"SuccessfulExit": False}

    def test_the_agents_path_has_no_sbin(self, tmp_path):
        """The guard calls each program by its full path: launchd's PATH
        for the agent is the one written here, and nothing else."""
        import plistlib

        from stacklets.ai import omlx_service

        agent = omlx_service.agent(tmp_path, "/opt/homebrew/opt/omlx/bin/omlx")

        path = plistlib.loads(agent.plist.encode())["EnvironmentVariables"]["PATH"]
        assert "sbin" not in path

    def test_with_the_app_running_omlx_does_not_start(self, guard):
        guard.app_starts()
        guard.wait_until_the_app_runs()

        assert guard.runs().wait(timeout=HANG) == 0
        assert not guard.omlx_ran()
        assert "oMLX app runs" in guard.log()

    def test_an_app_that_starts_during_the_wait_keeps_omlx_down(self, guard):
        """At login the app may start a moment after launchd started the agent."""
        guard.app_starts(after=0.5)

        assert guard.runs(settle=2).wait(timeout=HANG) == 0
        assert not guard.omlx_ran()

    def test_omlx_stops_when_the_app_starts_after_it(self, guard):
        started = guard.runs()
        guard.wait_until_omlx_runs()

        guard.app_starts()

        assert started.wait(timeout=HANG) == 0
        assert guard.omlx_termed()
        assert "oMLX app started" in guard.log()

    def test_a_stop_reaches_omlx_at_once(self, guard):
        """`stack down ai`: launchd sends TERM, also in the middle of the
        guard's wait between two looks for the app. The wait here is far
        longer than `HANG`, so a guard that sat it out would fail."""
        import signal

        started = guard.runs(poll=100 * HANG)
        guard.wait_until_omlx_runs()

        started.send_signal(signal.SIGTERM)

        assert started.wait(timeout=HANG) == 0
        assert guard.omlx_termed()

    def test_a_process_that_only_names_the_apps_path_does_not_stop_omlx(self, guard):
        import time

        guard.a_process_names_the_app()
        started = guard.runs()
        guard.wait_until_omlx_runs()
        time.sleep(1)   # five looks for the app at `poll` 0.2

        assert started.poll() is None, guard.log()
        assert not guard.omlx_termed()

    @pytest.mark.parametrize("executable, is_the_app", [
        ("/Applications/oMLX.app/Contents/MacOS/oMLX", True),
        ("/Volumes/Macintosh HD 2/Applications/oMLX.app/Contents/MacOS/oMLX", True),
        ("/Users/Shared Apps/Applications/oMLX.app/Contents/MacOS/oMLX", True),
        ("/Applications/oMLX.app/Contents/Resources/python/bin/python3", True),
        # A shell or `tail` with the app's path among its arguments.
        ("/bin/sh", False),
        ("/usr/bin/tail", False),
        # The stand-in of these tests.
        (f"/tmp/x/{STAND_IN_APP}/Contents/MacOS/oMLX", False),
    ])
    def test_the_shipped_check_finds_the_app_by_its_executable(
            self, tmp_path, monkeypatch, executable, is_the_app):
        """The check of a famstack instance on any Mac: `ps` stands in on
        PATH and lists one process by its executable, as `ps -axo comm=`
        does."""
        from stacklets.ai import omlx_app

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "ps").write_text(f"#!/bin/sh\necho '{executable}'\n")
        (bin_dir / "ps").chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
        monkeypatch.setattr(omlx_app, "PROCESS_COMMAND",
                            ("/bin/sh", "-c", omlx_app.app_check()))

        assert omlx_app.running() is is_the_app

    def test_omlx_ending_by_itself_is_started_again(self, guard):
        assert guard.runs(body="exit 0").wait(timeout=HANG) != 0

    def test_a_missing_omlx_stays_down_and_says_why(self, guard):
        """No restart every 10 s for a program that is not there."""
        assert guard.runs(body=None).wait(timeout=HANG) == 0
        assert "./stack up ai" in guard.log()

    def test_an_omlx_that_fails_at_its_start_stays_down_and_says_why(self, guard):
        """Port 42060 taken, or a broken install: oMLX ends at once. launchd
        would start it again every 10 s; the guard ends with 0 instead. The
        start here lasts longer than `HANG`, so it holds on a slow run too."""
        assert guard.runs(body="exit 1", startup=10 * HANG).wait(timeout=HANG) == 0
        assert "during its start" in guard.log()


class TestTheAppCheckOfARun:
    """`stack up ai`, `stack ai prepare` and `stack ai key new` stop while
    the oMLX app runs (`omlx_app.refuse_while_running`), and only then: a
    process that names the app's path is not the app. A `ps` that answers
    as macOS's does (`ps_stand_in.py`), told to look in the test's folder,
    as the guard's tests do."""

    @pytest.fixture
    def processes(self, tmp_path, monkeypatch):
        import shlex
        import subprocess

        from stacklets.ai import omlx_app

        app, bundle = stand_in_app(tmp_path)
        monkeypatch.setattr(omlx_app, "PROCESS_COMMAND",
                            ("/bin/sh", "-c", omlx_app.app_check(bundle, ps=macos_ps(tmp_path))))
        started: list = []

        class Processes:
            def app(self):
                started.append(subprocess.Popen(
                    ["/bin/sh", "-c", f"exec {shlex.quote(app)} 30"]))

            def naming_the_app(self):
                started.append(subprocess.Popen(["/bin/sh", "-c", "/bin/sleep 30", app]))

        yield Processes()
        for process in started:
            process.kill()
            process.wait()

    def test_a_process_that_names_the_apps_path_lets_the_run_go_on(self, processes):
        import time

        from stacklets.ai import omlx_app

        processes.naming_the_app()
        time.sleep(0.2)

        omlx_app.refuse_while_running()

    def test_the_app_stops_the_run(self, processes):
        import time

        from stack.hooks import Cancelled
        from stacklets.ai import omlx_app

        processes.app()
        time.sleep(0.2)

        with pytest.raises(Cancelled):
            omlx_app.refuse_while_running()
