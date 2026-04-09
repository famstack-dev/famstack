"""Command pattern: each CLI command is a class with execute() and format().

Commands encapsulate one CLI operation. They receive a Stack instance
and args, return a result dict, and optionally format it for the terminal.

The CLI dispatch becomes:
  command = registry.get(args.command)
  result = command.execute(stack, args)
  if pretty: print(command.format(result))
  else: print(json.dumps(result))
"""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))


@pytest.fixture(autouse=True)
def isolated_env():
    yield


def _make_stack(tmp_path, stacklets=None):
    from stack import Stack
    from stack.output import CollectorOutput

    (tmp_path / "stack.toml").write_text("""
[core]
timezone = "Europe/Berlin"
data_dir = "{data}"
""".format(data=tmp_path / "data"))
    stack_dir = tmp_path / ".stack"
    stack_dir.mkdir(exist_ok=True)
    (stack_dir / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')

    for sid, spec in (stacklets or {}).items():
        sdir = tmp_path / "stacklets" / sid
        hooks_dir = sdir / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        manifest = f'id = "{sid}"\nname = "{sid.title()}"\nversion = "0.1.0"\ncategory = "test"\n'
        if spec.get("requires"):
            requires = ", ".join(f'"{r}"' for r in spec["requires"])
            manifest += f"requires = [{requires}]\n"
        if spec.get("env_defaults"):
            manifest += "\n[env.defaults]\n"
            for k, v in spec["env_defaults"].items():
                manifest += f'{k} = "{v}"\n'
        if spec.get("generate"):
            keys = ", ".join(f'"{k}"' for k in spec["generate"])
            manifest += f"\n[env]\ngenerate = [{keys}]\n"
        (sdir / "stacklet.toml").write_text(manifest)
        for hook_name, content in spec.get("hooks", {}).items():
            (hooks_dir / hook_name).write_text(content)

    output = CollectorOutput()
    return Stack(root=tmp_path, data=tmp_path / "data", output=output), output


class TestEnvCommand:
    """EnvCommand renders environment variables for a stacklet."""

    def test_returns_rendered_env(self, tmp_path):
        from stack.commands import EnvCommand
        stck, _ = _make_stack(tmp_path, {"myapp": {
            "env_defaults": {"TZ": "{timezone}"},
        }})
        result = EnvCommand().execute(stck, stacklet="myapp")
        assert result["env"]["TZ"] == "Europe/Berlin"

    def test_error_for_unknown_stacklet(self, tmp_path):
        from stack.commands import EnvCommand
        stck, _ = _make_stack(tmp_path, {})
        result = EnvCommand().execute(stck, stacklet="nope")
        assert "error" in result


class TestListCommand:
    """ListCommand discovers all stacklets with their state."""

    def test_returns_stacklets(self, tmp_path):
        from stack.commands import ListCommand
        stck, _ = _make_stack(tmp_path, {"myapp": {}, "other": {}})
        result = ListCommand().execute(stck)
        ids = {s["id"] for s in result["stacklets"]}
        assert "myapp" in ids
        assert "other" in ids

    def test_includes_counts(self, tmp_path):
        from stack.commands import ListCommand
        stck, _ = _make_stack(tmp_path, {"myapp": {}})
        result = ListCommand().execute(stck)
        assert "total" in result
        assert "online" in result


class TestUpCommand:
    """UpCommand brings a stacklet up through the Stack lifecycle."""

    def test_returns_ok(self, tmp_path):
        from stack.commands import UpCommand
        stck, _ = _make_stack(tmp_path, {"myapp": {}})
        result = UpCommand().execute(stck, stacklet="myapp")
        assert result["ok"]

    def test_blocks_on_missing_dep(self, tmp_path):
        from stack.commands import UpCommand
        stck, _ = _make_stack(tmp_path, {
            "base": {},
            "app": {"requires": ["base"]},
        })
        result = UpCommand().execute(stck, stacklet="app")
        assert "error" in result

    def test_first_run_flag(self, tmp_path):
        from stack.commands import UpCommand
        stck, _ = _make_stack(tmp_path, {"myapp": {}})
        r1 = UpCommand().execute(stck, stacklet="myapp")
        r2 = UpCommand().execute(stck, stacklet="myapp")
        assert r1["first_run"] is True
        assert r2["first_run"] is False


class TestDownCommand:
    """DownCommand stops a stacklet."""

    def test_returns_ok(self, tmp_path):
        from stack.commands import DownCommand
        stck, _ = _make_stack(tmp_path, {"myapp": {}})
        stck.up("myapp")
        result = DownCommand().execute(stck, stacklet="myapp")
        assert result["ok"]


class TestDestroyCommand:
    """DestroyCommand removes a stacklet completely."""

    def test_returns_ok(self, tmp_path):
        from stack.commands import DestroyCommand
        stck, _ = _make_stack(tmp_path, {"myapp": {"generate": ["SECRET"]}})
        stck.up("myapp")
        result = DestroyCommand().execute(stck, stacklet="myapp")
        assert result["ok"]

    def test_clears_secrets(self, tmp_path):
        from stack.commands import DestroyCommand
        stck, _ = _make_stack(tmp_path, {"myapp": {"generate": ["SECRET"]}})
        stck.up("myapp")
        assert stck.secrets.get("myapp", "SECRET") is not None

        DestroyCommand().execute(stck, stacklet="myapp")
        assert stck.secrets.get("myapp", "SECRET") is None

    def test_removes_marker(self, tmp_path):
        from stack.commands import DestroyCommand
        stck, _ = _make_stack(tmp_path, {"myapp": {}})
        stck.up("myapp")
        assert (tmp_path / ".stack" / "myapp.setup-done").exists()

        DestroyCommand().execute(stck, stacklet="myapp")
        assert not (tmp_path / ".stack" / "myapp.setup-done").exists()
