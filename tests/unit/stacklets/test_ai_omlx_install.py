"""Installing oMLX: the Homebrew steps `stack up ai` runs on first boot.

Homebrew 6 refuses to load a formula from a tap outside its own
repositories until that tap is trusted. famstack publishes oMLX through
one, so without an explicit trust step `brew install` exits non-zero,
`on_install` fails, and the AI stacklet cannot be set up at all -- on
any machine with a current Homebrew, for every new user.

These pin the shape of that install: what runs, in what order, and that
an older Homebrew with no trust gate still reaches the install.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

# Every stacklet has a hook module named `on_install`; as a module of its
# stacklet's package, this one is `stacklets.ai.hooks.on_install`.
from stacklets.ai.hooks import on_install as _ai_on_install  # noqa: E402

from stacklets.ai import omlx_service  # noqa: E402

OMLX_TAP = _ai_on_install.OMLX_TAP
_install_omlx_formula = _ai_on_install._install_omlx_formula


class FakeCtx:
    """Records the shell commands a hook issues.

    `no_trust_subcommand` reproduces a pre-6 Homebrew, where `brew trust`
    does not exist and ctx.shell raises.
    """

    def __init__(self, *, no_trust_subcommand: bool = False):
        self.commands: list[str] = []
        self.steps: list[str] = []
        self._no_trust = no_trust_subcommand

    def step(self, msg):
        self.steps.append(msg)

    def shell(self, cmd):
        self.commands.append(cmd)
        if self._no_trust and cmd.startswith("brew trust"):
            raise RuntimeError("Unknown command: trust")
        return ""

    def shell_live(self, cmd):
        self.commands.append(cmd)
        return ""


class TestOmlxInstallSteps:

    def test_the_tap_is_trusted_before_the_install_runs(self):
        """The whole point: an untrusted tap makes `brew install` fail."""
        ctx = FakeCtx()
        _install_omlx_formula(ctx)

        joined = " | ".join(ctx.commands)
        assert f"brew trust {OMLX_TAP}" in joined, "the trust step must run"

        trust_at = next(i for i, c in enumerate(ctx.commands)
                        if c.startswith("brew trust"))
        install_at = next(i for i, c in enumerate(ctx.commands)
                          if "brew install omlx" in c)
        assert trust_at < install_at, "trusting after installing is too late"

    def test_the_tap_is_added_before_it_is_trusted(self):
        ctx = FakeCtx()
        _install_omlx_formula(ctx)

        tap_at = next(i for i, c in enumerate(ctx.commands)
                      if c.startswith("brew tap"))
        trust_at = next(i for i, c in enumerate(ctx.commands)
                        if c.startswith("brew trust"))
        assert tap_at < trust_at

    def test_grammar_is_requested_so_json_mode_works(self):
        # Structured classification needs xgrammar; without the flag the
        # formula installs a build whose JSON mode is broken.
        ctx = FakeCtx()
        _install_omlx_formula(ctx)
        assert any(c.endswith("brew install omlx --with-grammar") for c in ctx.commands)

    def test_homebrew_does_not_stop_to_ask(self):
        """Homebrew 7 asks before an install that brings dependencies. Deep
        in `stack up ai` nobody expects that question, and a script or an
        agent running it waits on it for good."""
        ctx = FakeCtx()
        _install_omlx_formula(ctx)
        (install,) = [c for c in ctx.commands if "brew install" in c]
        assert install.startswith("HOMEBREW_NO_ASK=1 ")

    def test_steps_are_separate_commands_not_one_chained_string(self):
        """Chained with `&&`, any failure reported the whole string, so a
        trust refusal read as a tap failure. Separate commands make the
        failing step the one that gets named."""
        ctx = FakeCtx()
        _install_omlx_formula(ctx)
        assert not any("&&" in c for c in ctx.commands)

    def test_a_homebrew_without_a_trust_gate_still_installs(self):
        """`brew trust` arrived in Homebrew 6. On older versions there is
        nothing to clear, so its absence is success, not a failed install."""
        ctx = FakeCtx(no_trust_subcommand=True)
        _install_omlx_formula(ctx)  # must not raise
        assert any("brew install omlx" in c for c in ctx.commands)


class TestWaitingForOmlx:
    """oMLX 0.7 answers `/v1/models` with 401 unless the request carries the key.

    The install writes that key into oMLX's settings, so its readiness
    check has to send it, or it waits out its whole timeout on a server
    that is up and warns that it is not.
    """

    def test_a_server_that_wants_the_key_counts_as_up(self, httpserver):
        httpserver.expect_request(
            "/v1/models", headers={"Authorization": "Bearer local"},
        ).respond_with_json({"data": []})
        httpserver.expect_request("/v1/models").respond_with_data("API key required", status=401)

        assert omlx_service.answers(httpserver.url_for("/v1"), "local", timeout=5)

    def test_a_server_that_refuses_the_key_is_not_up(self, httpserver):
        httpserver.expect_request("/v1/models").respond_with_data("API key required", status=401)

        assert not omlx_service.answers(httpserver.url_for("/v1"), "local", timeout=1)


# ── famstack's own oMLX ──────────────────────────────────────────────────

class TestFamstacksOwnOmlx:
    """Route 1: the oMLX famstack installs runs in a folder of its own,
    `<data_dir>/ai/omlx`, under famstack's own LaunchAgent, with its own
    settings, key and port. The oMLX app keeps `~/.omlx`; the two share
    only the models folder, `~/.omlx/models`, so nothing is downloaded
    twice. An oMLX already installed with Homebrew is used as a program,
    never with its settings.

    The install runs as `stack up ai` runs it, through a real hook context
    on a throwaway instance, which records every command line the hook
    runs. `brew`, `launchctl` and `curl` first on PATH record their calls: Homebrew has oMLX 0.8.0rc1, nothing is loaded in
    launchd yet. oMLX answers at once, at `[ai] openai_url`. The home directory is the
    test's, with the oMLX app's settings in it."""

    APP_SETTINGS = {"server": {"port": 8000}, "auth": {"api_key": "the-apps-own-key"}}

    @pytest.fixture
    def mac(self, tmp_path, monkeypatch, httpserver):
        import json
        import os

        from stack import Stack
        from stack.hooks import build_hook_ctx
        from stack.output import CollectorOutput

        home, bin_dir, calls = tmp_path / "home", tmp_path / "bin", tmp_path / "calls"
        (home / ".omlx").mkdir(parents=True)
        (home / ".omlx" / "settings.json").write_text(json.dumps(self.APP_SETTINGS))
        bin_dir.mkdir()
        commands = {
            "brew": 'if [ "$1 $2" = "list --versions" ]; then echo "omlx 0.8.0rc1"; fi',
            "launchctl": '[ "$1" = "list" ] && exit 1; exit 0',
            # A `curl` the hook might start succeeds, as oMLX answering would.
            "curl": "exit 0",
        }
        for name, body in commands.items():
            (bin_dir / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{calls}"\n{body}\n')
            (bin_dir / name).chmod(0o755)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        (tmp_path / "stack.toml").write_text(
            f'[core]\ndata_dir = "{tmp_path / "data"}"\n\n'
            f'[ai]\nprovider = "managed"\nopenai_url = "{httpserver.url_for("/v1")}"\n'
            'openai_key = "local"\nspeech = false\n')
        # famstack's new oMLX lists its models once it started.
        httpserver.expect_request("/v1/models").respond_with_json({"data": []})
        (tmp_path / ".stack").mkdir()
        stack = Stack(_REPO_ROOT, tmp_path / "data", instance_dir=tmp_path,
                      output=CollectorOutput())
        ctx = build_hook_ctx("ai", env=stack.env("ai"), step_fn=stack.output.step, stack=stack)
        # Every command line the hook runs, also those whose program is no
        # stand-in: a command line is visible to every process on the Mac.
        command_lines: list[str] = []
        for name in ("shell", "shell_live"):
            run = getattr(ctx, name)
            setattr(ctx, name, lambda line, _run=run: (command_lines.append(line), _run(line))[1])

        class Mac:
            folder = tmp_path / "data" / "ai" / "omlx"
            app_settings = home / ".omlx" / "settings.json"
            models = home / ".omlx" / "models"
            agent = home / "Library" / "LaunchAgents" / "dev.famstack.omlx.plist"

            @staticmethod
            def installs():
                state = tmp_path / "state"
                state.mkdir(exist_ok=True)
                _ai_on_install.install_omlx(ctx, state)

            @staticmethod
            def calls() -> list[str]:
                return calls.read_text().splitlines() if calls.exists() else []

            @staticmethod
            def command_lines() -> list[str]:
                """Every command line the hook ran, in order."""
                return command_lines

            @staticmethod
            def ai() -> dict:
                import tomllib
                return tomllib.loads((tmp_path / "stack.toml").read_text())["ai"]

        return Mac()

    def test_the_omlx_apps_settings_stay_as_they_are(self, mac):
        before = mac.app_settings.read_bytes()

        mac.installs()

        assert mac.app_settings.read_bytes() == before

    def test_famstacks_omlx_gets_its_own_key_port_and_the_shared_models(self, mac):
        """The key is a new one, never the app's, and the stack sends it."""
        import json

        mac.installs()

        settings = json.loads((mac.folder / "settings.json").read_text())
        key = settings["auth"]["api_key"]
        assert settings["server"]["port"] == 42060
        assert settings["model"]["model_dir"] == str(mac.models)
        assert key not in ("the-apps-own-key", "local")
        assert mac.ai()["openai_key"] == key

    def test_it_runs_under_famstacks_launch_agent_in_its_folder(self, mac):
        """`brew services` would run oMLX with `~/.omlx`: its service passes
        no base folder. Homebrew's oMLX is used as a program only."""
        mac.installs()

        import plistlib
        import shlex
        [guard] = plistlib.loads(mac.agent.read_bytes())["ProgramArguments"]
        assert f"serve --base-path {shlex.quote(str(mac.folder))}" in Path(guard).read_text()
        assert any(c.startswith("launchctl load") and str(mac.agent) in c for c in mac.calls())
        assert not any(c.startswith("brew services") or c.startswith("brew install")
                       for c in mac.calls()), mac.calls()

    def test_the_run_names_the_installed_version_and_never_the_key(self, mac, capsys):
        """Homebrew installs the release its tap offers, which can be a
        release candidate; the run says which one runs."""
        mac.installs()

        out = capsys.readouterr()
        assert "0.8.0rc1" in out.out + out.err
        assert mac.ai()["openai_key"] not in out.out + out.err

    def test_the_key_never_goes_on_a_command_line(self, mac):
        """A command line is visible to every process on the Mac."""
        mac.installs()

        key = mac.ai()["openai_key"]
        assert mac.command_lines(), "the hook ran no command: the recording is broken"
        assert not [c for c in mac.command_lines() + mac.calls() if key in c], mac.command_lines()


def test_no_hint_of_the_ai_stacklet_prints_its_key():
    """`stack up ai` ends with the stacklet's hints, rendered with the
    instance's settings. The AI's key must never be printed there."""
    import re
    import tomllib

    manifest = tomllib.loads((_REPO_ROOT / "stacklets" / "ai" / "stacklet.toml").read_text())

    # A template such as `{ai_openai_key}` is filled with the key itself.
    assert not any(re.search(r"\{[^}]*key[^}]*\}", hint) for hint in manifest.get("hints", []))


def test_a_start_reaches_the_agent_in_a_home_with_a_quote(tmp_path, monkeypatch):
    """famstack's oMLX is loaded through a shell line. A home such as
    `/Users/o'brien` reaches `launchctl` as one path."""
    import os
    import subprocess
    import types

    from stacklets.ai import omlx_service

    home = tmp_path / "o'brien"
    monkeypatch.setattr(Path, "home", lambda: home)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "launchctl").write_text(
        f'#!/bin/sh\n[ "$1" = "list" ] && exit 113\nprintf "%s\\n" "$2" >> "{tmp_path}/paths"\n')
    (bin_dir / "launchctl").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"}

    def shell(line):
        done = subprocess.run(line, shell=True, env=env, capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(line)
        return done.stdout

    omlx_service.start(types.SimpleNamespace(shell=shell), tmp_path / "data",
                       "/opt/homebrew/opt/omlx/bin/omlx", changed=False)

    assert (tmp_path / "paths").read_text().splitlines() == [str(omlx_service.agent_file())]


def test_the_launch_agent_holds_a_data_dir_with_an_ampersand(tmp_path):
    """A data dir such as `~/Homer & Marge` is written into famstack's
    oMLX LaunchAgent as it is: launchd reads the same paths back."""
    import plistlib

    from stacklets.ai import omlx_service

    import shlex

    data_dir = tmp_path / "Homer & Marge <data>"

    written = omlx_service.agent(data_dir, "/opt/homebrew/opt/omlx/bin/omlx")
    agent = plistlib.loads(written.plist.encode())

    base = str(omlx_service.folder(data_dir))
    guard = omlx_service.guard_file(data_dir)
    assert agent["ProgramArguments"] == [str(guard)]
    assert f"serve --base-path {shlex.quote(base)}" in written.files[guard]
    assert agent["WorkingDirectory"] == base
    assert agent["Label"] == "dev.famstack.omlx"
