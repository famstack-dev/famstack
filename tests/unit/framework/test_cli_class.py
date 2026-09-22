"""CLI class: orchestrates Stack + Docker for complete lifecycle operations.

The CLI class adds Docker operations on top of Stack's framework logic.
These tests verify the orchestration without actual Docker — we test
that the right methods are called in the right order.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "lib"))


@pytest.fixture(autouse=True)
def isolated_env():
    """Mock Docker so CLI tests don't need a running daemon."""
    with patch("stack.docker.ensure_network", return_value=("mocked", None)), \
         patch("stack.docker.compose_up", return_value=(0, "")), \
         patch("stack.docker.compose_stop", return_value=(0, "")), \
         patch("stack.docker.compose_down", return_value=(0, "")):
        yield


def _make_cli(tmp_path, stacklets=None):
    from stack import Stack
    from stack.cli import CLI
    from stack.output import CollectorOutput

    (tmp_path / "stack.toml").write_text("""
[core]
timezone = "Europe/Berlin"
""")
    stack_dir = tmp_path / ".stack"
    stack_dir.mkdir(exist_ok=True)
    (stack_dir / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')

    for sid, spec in (stacklets or {}).items():
        sdir = tmp_path / "stacklets" / sid
        hooks_dir = sdir / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        manifest = f'id = "{sid}"\nname = "{sid.title()}"\nversion = "0.1.0"\ncategory = "test"\n'
        if spec.get("requires"):
            requires_list = ", ".join(f'"{d}"' for d in spec["requires"])
            manifest += f"requires = [{requires_list}]\n"
        if spec.get("env_defaults"):
            manifest += "\n[env.defaults]\n"
            for k, v in spec["env_defaults"].items():
                manifest += f'{k} = "{v}"\n'
        (sdir / "stacklet.toml").write_text(manifest)

    output = CollectorOutput()
    stck = Stack(root=tmp_path, data=tmp_path / "data", output=output)
    cli = CLI(stck)
    return cli, output


def _create_minimal_stacklet(root, sid):
    sdir = root / "stacklets" / sid
    (sdir / "hooks").mkdir(parents=True, exist_ok=True)
    (sdir / "stacklet.toml").write_text(
        f'id = "{sid}"\nname = "{sid.title()}"\nversion = "0.1.0"\ncategory = "test"\n'
    )


class TestCLIUp:
    """CLI.up() runs Stack.up() then Docker operations."""

    def test_returns_ok_without_docker(self, tmp_path):
        """Without a compose file, up succeeds with just framework logic."""
        cli, output = _make_cli(tmp_path, {"myapp": {}})
        result = cli.up("myapp")
        assert result["ok"]

    def test_reports_first_run(self, tmp_path):
        cli, output = _make_cli(tmp_path, {"myapp": {}})
        r1 = cli.up("myapp")
        r2 = cli.up("myapp")
        assert r1["first_run"] is True
        assert r2["first_run"] is False


class TestSetupDoneMarkerGating:
    """The setup-done marker represents 'fully bootstrapped' — it should
    only be touched after on_install_success completes (or trivially
    succeeds because the hook doesn't exist). If anything in the first-run
    chain fails before that point, a retry must re-run from scratch."""

    def test_stack_up_alone_does_not_touch_marker(self, tmp_path):
        """Stack.up handles on_install but is not the end of first-run.
        The marker is promoted later, by the CLI layer, once post-install
        API work has run."""
        from stack import Stack
        _create_minimal_stacklet(tmp_path, "myapp")
        stck = Stack(root=tmp_path, data=tmp_path / "data")
        stck.up("myapp")
        assert not stck._setup_done_marker("myapp").exists()

    def test_marker_touched_when_no_install_success_hook(self, tmp_path):
        """Stacklets without an on_install_success hook are trivially
        bootstrapped after on_install — framework promotes the marker."""
        cli, _ = _make_cli(tmp_path, {"myapp": {}})
        cli.up("myapp")
        assert (tmp_path / ".stack" / "myapp.setup-done").exists()

    def test_marker_not_touched_when_install_success_fails(self, tmp_path):
        """If on_install_success raises/returns False, `cli.up` reports
        an error and the marker stays absent — a retry re-enters the
        full bootstrap so transient failures don't leave the stacklet
        stuck looking 'installed' when it isn't."""
        cli, _ = _make_cli(tmp_path, {"myapp": {}})
        sdir = tmp_path / "stacklets" / "myapp"
        (sdir / "hooks" / "on_install_success.py").write_text(
            "def run(ctx):\n"
            "    raise RuntimeError('post-install failed')\n"
        )
        marker = tmp_path / ".stack" / "myapp.setup-done"

        r1 = cli.up("myapp")
        assert "error" in r1
        assert not marker.exists()

        # Retry sees the same failure — still no marker, still an error.
        r2 = cli.up("myapp")
        assert "error" in r2
        assert not marker.exists()


class TestRefreshCore:
    """`_refresh_core` re-renders core's env after a sibling stacklet
    populates a secret core consumes (e.g. docs__API_TOKEN) and runs
    `compose up` against core. Because `compose_up` itself is now
    unconditionally force-recreate (compose's hash doesn't cover
    env_file *contents*), the bot-runner reliably picks up the fresh
    token — the bug that previously left 'Documents Room' invisible
    until the user manually bounced core."""

    def _setup(self, tmp_path):
        """A stack root with a core stacklet that has a docker-compose.yml,
        plus a sibling whose `_refresh_core` call we want to test."""
        from stack import Stack
        from stack.output import CollectorOutput

        (tmp_path / "stack.toml").write_text("[core]\ntimezone = \"Europe/Berlin\"\n")
        (tmp_path / ".stack").mkdir(exist_ok=True)
        (tmp_path / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')

        for sid in ("core", "docs"):
            sdir = tmp_path / "stacklets" / sid
            (sdir / "hooks").mkdir(parents=True, exist_ok=True)
            (sdir / "stacklet.toml").write_text(
                f'id = "{sid}"\nname = "{sid.title()}"\nversion = "0.1.0"\ncategory = "test"\n'
            )
        (tmp_path / "stacklets" / "core" / "docker-compose.yml").write_text(
            "name: stack-core\nservices: {}\n"
        )

        return Stack(root=tmp_path, data=tmp_path / "data", output=CollectorOutput())

    def test_calls_compose_up_against_core_when_core_running(self, tmp_path, monkeypatch):
        from stack.cli import _refresh_core
        stck = self._setup(tmp_path)

        monkeypatch.setattr("stack.docker.running_project_ids", lambda: {"core", "docs"})
        calls = []

        def fake_up(compose_file, env=None):
            calls.append(str(compose_file))
            return (0, "")

        monkeypatch.setattr("stack.docker.compose_up", fake_up)

        _refresh_core(stck, "docs")

        assert len(calls) == 1, "expected one compose_up call against core"
        assert calls[0].endswith("stacklets/core/docker-compose.yml")

    def test_skips_when_core_not_running(self, tmp_path, monkeypatch):
        from stack.cli import _refresh_core
        stck = self._setup(tmp_path)

        monkeypatch.setattr("stack.docker.running_project_ids", lambda: {"docs"})
        calls = []
        monkeypatch.setattr("stack.docker.compose_up",
                            lambda *a, **kw: (calls.append(a), (0, ""))[1])

        _refresh_core(stck, "docs")
        assert calls == []

    def test_skips_when_target_is_core(self, tmp_path, monkeypatch):
        from stack.cli import _refresh_core
        stck = self._setup(tmp_path)

        monkeypatch.setattr("stack.docker.running_project_ids", lambda: {"core"})
        calls = []
        monkeypatch.setattr("stack.docker.compose_up",
                            lambda *a, **kw: (calls.append(a), (0, ""))[1])

        _refresh_core(stck, "core")
        assert calls == []


class TestCLIDown:
    """CLI.down() runs Stack.down() then Docker compose stop."""

    def test_returns_ok_without_docker(self, tmp_path):
        cli, _ = _make_cli(tmp_path, {"myapp": {}})
        cli.up("myapp")
        result = cli.down("myapp")
        assert result["success"]

    def test_unknown_stacklet_fails(self, tmp_path):
        cli, _ = _make_cli(tmp_path, {})
        result = cli.down("nope")
        assert "error" in result

    def test_down_refreshes_env_even_when_env_exists(self, tmp_path, monkeypatch):
        """A stale .env must be re-rendered before compose stop.

        compose reads the whole file up front, so a .env missing a
        variable a later merge added makes stop fail and the stacklet
        unstoppable. up already refreshes; down must too. Refreshing only
        when .env was absent was the gap that made memory unstoppable
        after MEDIA_ARCHIVE_DIR was added to its compose mounts.
        """
        cli, _ = _make_cli(tmp_path, {"myapp": {}})
        sdir = tmp_path / "stacklets" / "myapp"
        (sdir / "docker-compose.yml").write_text("services: {}\n")
        (sdir / ".env").write_text("STALE=1\n")  # exists, but out of date
        refreshed = []
        monkeypatch.setattr(cli.stack, "refresh_env",
                            lambda sid: refreshed.append(sid) or {})
        with patch("stack.docker.compose_stop", return_value=(0, "")):
            result = cli.down("myapp")
        assert result["success"]
        assert refreshed == ["myapp"], "down must re-render .env before compose acts"

    def test_down_surfaces_the_compose_error(self, tmp_path, monkeypatch):
        """A failed stop reports the real cause, not "unknown error".

        The result used to carry only `output`, which the caller dropped,
        so the operator saw "unknown error" for a specific compose fault.
        """
        cli, _ = _make_cli(tmp_path, {"myapp": {}})
        sdir = tmp_path / "stacklets" / "myapp"
        (sdir / "docker-compose.yml").write_text("services: {}\n")
        monkeypatch.setattr(cli.stack, "refresh_env", lambda sid: {})
        fault = "invalid spec: :/vault/media:ro: empty section between colons"
        with patch("stack.docker.compose_stop", return_value=(1, fault)):
            result = cli.down("myapp")
        assert not result["success"]
        assert fault in result["error"]


class TestCLIDownAll:
    """`stack down all` stops every currently-running stacklet in reverse
    dependency order — dependents first so their deps outlive them."""

    def test_down_all_no_stacklets_running_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setattr("stack.docker.running_project_ids", lambda: set())
        cli, _ = _make_cli(tmp_path, {"myapp": {}})
        result = cli.down("all")
        assert result["ok"]
        assert result["stopped"] == []

    def test_down_all_stops_only_running_stacklets(self, tmp_path, monkeypatch):
        monkeypatch.setattr("stack.docker.running_project_ids", lambda: {"myapp"})
        cli, _ = _make_cli(tmp_path, {"myapp": {}, "other": {}})
        cli.up("myapp")
        result = cli.down("all")
        assert result["ok"]
        assert result["stopped"] == ["myapp"]

    def test_down_all_orders_dependents_first(self, tmp_path, monkeypatch):
        # c requires b; b requires a. Shutdown order should be c → b → a
        # (reverse of up order) so deps outlive their dependents.
        monkeypatch.setattr("stack.docker.running_project_ids",
                            lambda: {"a", "b", "c"})
        cli, _ = _make_cli(tmp_path, {
            "a": {},
            "b": {"requires": ["a"]},
            "c": {"requires": ["b"]},
        })
        for sid in ("a", "b", "c"):
            cli.up(sid)
        result = cli.down("all")
        assert result["ok"]
        assert result["stopped"] == ["c", "b", "a"]


class TestCLIManyStacklets:
    """`stack down a b c` and `stack restart a,b` act on every id named.

    The parser used to take one id and drop the rest without a word, so
    `stack down memory code docs` stopped memory and reported success.
    Several ids are ordered the way `all` is: dependents stop first,
    dependencies start first.
    """

    def test_ids_split_on_spaces_and_commas(self):
        from stack.cli import _stacklet_ids
        assert _stacklet_ids(["a,b", "c", "b,", " d "]) == ["a", "b", "c", "d"]

    def test_down_many_stops_each_named_stacklet_dependents_first(self, tmp_path):
        cli, _ = _make_cli(tmp_path, {
            "a": {},
            "b": {"requires": ["a"]},
            "c": {"requires": ["b"]},
            "other": {},
        })
        for sid in ("a", "b", "c", "other"):
            cli.up(sid)
        result = cli.down_many(["a", "c", "b"])
        assert result["ok"]
        assert result["stopped"] == ["c", "b", "a"]

    def test_up_many_starts_dependencies_first(self, tmp_path):
        cli, _ = _make_cli(tmp_path, {
            "a": {},
            "b": {"requires": ["a"]},
        })
        result = cli.up_many(["b", "a"])
        assert result["ok"]
        assert result["started"] == ["a", "b"]

    def test_unknown_id_stops_nothing(self, tmp_path, monkeypatch):
        """A typo in one id must not leave the others half stopped."""
        cli, _ = _make_cli(tmp_path, {"a": {}})
        cli.up("a")
        stopped = []
        monkeypatch.setattr(cli, "down", lambda sid: stopped.append(sid) or {"success": True})
        result = cli.down_many(["a", "nope"])
        assert not result["ok"]
        assert "nope" in result["error"]
        assert stopped == []


class TestHandlersTakeManyIds:
    """The command handlers read several ids off the command line."""

    def test_up_starts_every_named_stacklet(self, tmp_path, monkeypatch):
        from argparse import Namespace
        from stack.cli import handle_up
        monkeypatch.setattr("stack.docker.running_project_ids", lambda: set())
        cli, _ = _make_cli(tmp_path, {"a": {}, "b": {"requires": ["a"]}})
        handle_up(cli.stack, Namespace(stacklet=["b,a"], no_voice=False))
        assert cli.stack.is_installed("a") and cli.stack.is_installed("b")

    def test_destroy_removes_every_named_stacklet(self, tmp_path, monkeypatch):
        from argparse import Namespace
        from stack.cli import handle_destroy
        monkeypatch.setattr("stack.docker.running_project_ids", lambda: set())
        cli, _ = _make_cli(tmp_path, {"a": {}, "b": {"requires": ["a"]}, "c": {}})
        for sid in ("a", "b", "c"):
            cli.up(sid)
        handle_destroy(cli.stack, Namespace(stacklet=["a", "b"], yes=True))
        assert not cli.stack.is_installed("a")
        assert not cli.stack.is_installed("b")
        assert cli.stack.is_installed("c")

    def test_destroy_with_an_unknown_id_destroys_nothing(self, tmp_path, monkeypatch):
        from argparse import Namespace
        from stack.cli import handle_destroy
        cli, _ = _make_cli(tmp_path, {"a": {}})
        cli.up("a")
        with pytest.raises(SystemExit):
            handle_destroy(cli.stack, Namespace(stacklet=["a", "nope"], yes=True))
        assert cli.stack.is_installed("a")


class TestCLIDestroy:
    """CLI.destroy() runs Docker compose down then Stack.destroy()."""

    def test_returns_ok_without_docker(self, tmp_path):
        cli, _ = _make_cli(tmp_path, {"myapp": {}})
        cli.up("myapp")
        result = cli.destroy("myapp")
        assert result["ok"]

    def test_clears_marker(self, tmp_path):
        cli, _ = _make_cli(tmp_path, {"myapp": {}})
        cli.up("myapp")
        assert (tmp_path / ".stack" / "myapp.setup-done").exists()

        cli.destroy("myapp")
        assert not (tmp_path / ".stack" / "myapp.setup-done").exists()
