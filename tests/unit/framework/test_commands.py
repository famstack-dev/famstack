"""The Stack methods behind `stack env`, `list`, `up`, `down` and `destroy`.

Each returns the dict the CLI prints, so these tests pin what a caller of
the command sees, without Docker.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "lib"))


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


class TestStackEnv:
    """Stack.env renders environment variables for a stacklet."""

    def test_returns_rendered_env(self, tmp_path):
        stck, _ = _make_stack(tmp_path, {"myapp": {
            "env_defaults": {"TZ": "{timezone}"},
        }})
        assert stck.env("myapp")["TZ"] == "Europe/Berlin"

    def test_error_for_unknown_stacklet(self, tmp_path):
        stck, _ = _make_stack(tmp_path, {})
        with pytest.raises(ValueError):
            stck.env("nope")


@pytest.mark.usefixtures("docker_with_no_containers")
class TestStackList:
    """Stack.list discovers all stacklets with their state."""

    def test_returns_stacklets(self, tmp_path):
        stck, _ = _make_stack(tmp_path, {"myapp": {}, "other": {}})
        result = stck.list()
        ids = {s["id"] for s in result["stacklets"]}
        assert "myapp" in ids
        assert "other" in ids

    def test_includes_counts(self, tmp_path):
        stck, _ = _make_stack(tmp_path, {"myapp": {}})
        result = stck.list()
        assert "total" in result
        assert "online" in result


class TestStackUp:
    """Stack.up brings a stacklet up through the Stack lifecycle."""

    def test_returns_ok(self, tmp_path):
        stck, _ = _make_stack(tmp_path, {"myapp": {}})
        result = stck.up("myapp")
        assert result["ok"]

    def test_blocks_on_missing_dep(self, tmp_path):
        stck, _ = _make_stack(tmp_path, {
            "base": {},
            "app": {"requires": ["base"]},
        })
        result = stck.up("app")
        assert "error" in result

    def test_first_run_flag(self, tmp_path):
        # Stack.up does not promote the setup-done marker:
        # run_on_install_success does (owned by the CLI layer). Simulate the
        # full first-run cycle so the second call sees first_run=False.
        stck, _ = _make_stack(tmp_path, {"myapp": {}})
        r1 = stck.up("myapp")
        stck.run_on_install_success("myapp")
        r2 = stck.up("myapp")
        assert r1["first_run"] is True
        assert r2["first_run"] is False


class TestStackDown:
    """Stack.down stops a stacklet."""

    def test_returns_ok(self, tmp_path):
        stck, _ = _make_stack(tmp_path, {"myapp": {}})
        stck.up("myapp")
        result = stck.down("myapp")
        assert result["ok"]


class TestStackDestroy:
    """Stack.destroy removes a stacklet completely."""

    def test_returns_ok(self, tmp_path):
        stck, _ = _make_stack(tmp_path, {"myapp": {"generate": ["SECRET"]}})
        stck.up("myapp")
        result = stck.destroy("myapp")
        assert result["ok"]

    def test_clears_secrets(self, tmp_path):
        stck, _ = _make_stack(tmp_path, {"myapp": {"generate": ["SECRET"]}})
        stck.up("myapp")
        assert stck.secrets.get("myapp", "SECRET") is not None

        stck.destroy("myapp")
        assert stck.secrets.get("myapp", "SECRET") is None

    def test_removes_marker(self, tmp_path):
        # The setup-done marker is promoted by run_on_install_success —
        # call it explicitly here to simulate a fully-installed stacklet
        # before destroy.
        stck, _ = _make_stack(tmp_path, {"myapp": {}})
        stck.up("myapp")
        stck.run_on_install_success("myapp")
        assert (tmp_path / ".stack" / "myapp.setup-done").exists()

        stck.destroy("myapp")
        assert not (tmp_path / ".stack" / "myapp.setup-done").exists()
