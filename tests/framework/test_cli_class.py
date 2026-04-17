"""CLI class: orchestrates Stack + Docker for complete lifecycle operations.

The CLI class adds Docker operations on top of Stack's framework logic.
These tests verify the orchestration without actual Docker — we test
that the right methods are called in the right order.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))


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
