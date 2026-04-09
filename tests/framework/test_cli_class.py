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
        if spec.get("env_defaults"):
            manifest += "\n[env.defaults]\n"
            for k, v in spec["env_defaults"].items():
                manifest += f'{k} = "{v}"\n'
        (sdir / "stacklet.toml").write_text(manifest)

    output = CollectorOutput()
    stck = Stack(root=tmp_path, data=tmp_path / "data", output=output)
    cli = CLI(stck)
    return cli, output


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
