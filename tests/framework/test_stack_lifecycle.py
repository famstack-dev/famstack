"""Stack lifecycle: up, down, destroy.

These tests verify the lifecycle orchestration — the order in which
hooks fire, secrets generate, env renders, and markers get written.
No Docker — we test the framework logic, not container management.

The Stack class coordinates:
  up:      check deps → render env → on_install (first) → write .env → mark done
  down:    on_stop
  destroy: on_stop → on_destroy → clear secrets → delete marker → delete data
"""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))


@pytest.fixture(autouse=True)
def isolated_env():
    """Override conftest — lifecycle tests manage their own state."""
    yield


def _make_stack(tmp_path, stacklets=None, config_extra=""):
    """Create a Stack with optional stacklets and config.

    Each stacklet in the dict is {id: {hooks: {name: content}, ...}}.
    """
    from stack import Stack

    # Write config
    (tmp_path / "stack.toml").write_text(f"""
[core]
domain = ""
data_dir = "{tmp_path / 'data'}"
timezone = "Europe/Berlin"
{config_extra}
""")

    # Create .stack
    stack_dir = tmp_path / ".stack"
    stack_dir.mkdir(exist_ok=True)
    (stack_dir / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')

    # Create stacklets
    for sid, spec in (stacklets or {}).items():
        sdir = tmp_path / "stacklets" / sid
        hooks_dir = sdir / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)

        manifest = spec.get("manifest", "")
        if not manifest:
            manifest = f"""
id = "{sid}"
name = "{sid.title()}"
version = "0.1.0"
category = "test"
"""
            if spec.get("requires"):
                requires = ", ".join(f'"{r}"' for r in spec["requires"])
                manifest += f"requires = [{requires}]\n"

            if spec.get("generate"):
                keys = ", ".join(f'"{k}"' for k in spec["generate"])
                manifest += f"\n[env]\ngenerate = [{keys}]\n"

            if spec.get("env_defaults"):
                manifest += "\n[env.defaults]\n"
                for k, v in spec["env_defaults"].items():
                    manifest += f'{k} = "{v}"\n'

        (sdir / "stacklet.toml").write_text(manifest)

        for hook_name, hook_content in spec.get("hooks", {}).items():
            (hooks_dir / hook_name).write_text(hook_content)
            if hook_name.endswith(".sh"):
                (hooks_dir / hook_name).chmod(0o755)

    return Stack(root=tmp_path, data=tmp_path / "data")


# ── Stack.up ──────────────────────────────────────────────────────────────

class TestStackUp:
    """stack.up() brings a stacklet from available to running."""

    def test_renders_env(self, tmp_path):
        """up() returns rendered env vars."""
        s = _make_stack(tmp_path, {"myapp": {
            "env_defaults": {"TZ": "{timezone}", "DATA": "{data_dir}/myapp"},
        }})
        result = s.up("myapp")
        assert result["ok"]
        assert result["env"]["TZ"] == "Europe/Berlin"

    def test_generates_secrets(self, tmp_path):
        """up() generates declared secrets on first run."""
        s = _make_stack(tmp_path, {"myapp": {"generate": ["DB_PASSWORD"]}})
        s.up("myapp")
        assert s.secrets.get("myapp", "DB_PASSWORD") is not None

    def test_runs_on_install_hook(self, tmp_path):
        """on_install fires on first up. We verify by checking the marker
        it writes."""
        marker = tmp_path / "installed"
        s = _make_stack(tmp_path, {"myapp": {
            "hooks": {
                "on_install.py": textwrap.dedent(f"""
                    from pathlib import Path
                    def run(ctx):
                        Path("{marker}").write_text("yes")
                """),
            },
        }})
        s.up("myapp")
        assert marker.read_text() == "yes"

    def test_on_install_runs_only_once(self, tmp_path):
        """on_install doesn't fire on subsequent ups — gated by setup-done marker."""
        counter = tmp_path / "count"
        counter.write_text("0")
        s = _make_stack(tmp_path, {"myapp": {
            "hooks": {
                "on_install.py": textwrap.dedent(f"""
                    from pathlib import Path
                    def run(ctx):
                        p = Path("{counter}")
                        p.write_text(str(int(p.read_text()) + 1))
                """),
            },
        }})
        s.up("myapp")
        s.up("myapp")
        assert counter.read_text() == "1"

    def test_creates_setup_done_marker(self, tmp_path):
        """After first up, the setup-done marker exists."""
        s = _make_stack(tmp_path, {"myapp": {}})
        s.up("myapp")
        assert (tmp_path / ".stack" / "myapp.setup-done").exists()

    def test_first_run_flag(self, tmp_path):
        """First up returns first_run=True, second returns False."""
        s = _make_stack(tmp_path, {"myapp": {}})
        r1 = s.up("myapp")
        r2 = s.up("myapp")
        assert r1["first_run"] is True
        assert r2["first_run"] is False

    def test_writes_env_file(self, tmp_path):
        """up() writes a .env file in the stacklet directory."""
        s = _make_stack(tmp_path, {"myapp": {
            "env_defaults": {"TZ": "{timezone}"},
        }})
        s.up("myapp")
        env_file = tmp_path / "stacklets" / "myapp" / ".env"
        assert env_file.exists()
        assert "Europe/Berlin" in env_file.read_text()

    def test_unknown_stacklet_fails(self, tmp_path):
        s = _make_stack(tmp_path, {})
        result = s.up("nope")
        assert "error" in result


# ── Dependencies ──────────────────────────────────────────────────────────

class TestStackUpDependencies:
    """up() checks requires before doing any work."""

    def test_fails_when_dependency_missing(self, tmp_path):
        s = _make_stack(tmp_path, {
            "base": {},
            "app": {"requires": ["base"]},
        })
        result = s.up("app")
        assert "error" in result
        assert "base" in result["error"].lower()

    def test_passes_when_dependency_is_up(self, tmp_path):
        """If the dependency has been up'd, the requires check passes."""
        s = _make_stack(tmp_path, {
            "base": {},
            "app": {"requires": ["base"]},
        })
        s.up("base")
        result = s.up("app")
        assert result.get("ok"), f"Should pass: {result}"


# ── Stack.down ────────────────────────────────────────────────────────────

class TestStackDown:
    """stack.down() stops a stacklet. Data and setup state preserved."""

    def test_runs_on_stop_hook(self, tmp_path):
        marker = tmp_path / "stopped"
        s = _make_stack(tmp_path, {"myapp": {
            "hooks": {
                "on_stop.sh": f"#!/bin/bash\ntouch {marker}\n",
            },
        }})
        s.up("myapp")
        s.down("myapp")
        assert marker.exists()

    def test_preserves_setup_done_marker(self, tmp_path):
        """down doesn't remove the setup-done marker — up won't re-install."""
        s = _make_stack(tmp_path, {"myapp": {}})
        s.up("myapp")
        s.down("myapp")
        assert (tmp_path / ".stack" / "myapp.setup-done").exists()


# ── Stack.destroy ─────────────────────────────────────────────────────────

class TestStackDestroy:
    """stack.destroy() removes everything — back to available state."""

    def test_runs_on_destroy_hook(self, tmp_path):
        marker = tmp_path / "destroyed"
        s = _make_stack(tmp_path, {"myapp": {
            "hooks": {
                "on_destroy.py": textwrap.dedent(f"""
                    from pathlib import Path
                    def run(ctx):
                        Path("{marker}").write_text("gone")
                """),
            },
        }})
        s.up("myapp")
        s.destroy("myapp")
        assert marker.read_text() == "gone"

    def test_clears_stacklet_secrets(self, tmp_path):
        s = _make_stack(tmp_path, {"myapp": {"generate": ["DB_PASSWORD"]}})
        s.up("myapp")
        assert s.secrets.get("myapp", "DB_PASSWORD") is not None

        s.destroy("myapp")
        assert s.secrets.get("myapp", "DB_PASSWORD") is None

    def test_preserves_global_secrets(self, tmp_path):
        s = _make_stack(tmp_path, {"myapp": {"generate": ["DB_PASSWORD"]}})
        s.up("myapp")
        s.destroy("myapp")
        # global__ADMIN_PASSWORD was written by _make_stack
        assert s.secrets.get("myapp", "ADMIN_PASSWORD") == "test"

    def test_removes_setup_done_marker(self, tmp_path):
        s = _make_stack(tmp_path, {"myapp": {}})
        s.up("myapp")
        assert (tmp_path / ".stack" / "myapp.setup-done").exists()

        s.destroy("myapp")
        assert not (tmp_path / ".stack" / "myapp.setup-done").exists()

    def test_deletes_data_directory(self, tmp_path):
        s = _make_stack(tmp_path, {"myapp": {
            "hooks": {
                "on_install.py": textwrap.dedent(f"""
                    from pathlib import Path
                    def run(ctx):
                        d = Path("{tmp_path / 'data' / 'myapp'}")
                        d.mkdir(parents=True, exist_ok=True)
                        (d / "stuff.db").write_text("data")
                """),
            },
        }})
        s.up("myapp")
        assert (tmp_path / "data" / "myapp" / "stuff.db").exists()

        s.destroy("myapp")
        assert not (tmp_path / "data" / "myapp").exists()

    def test_removes_env_file(self, tmp_path):
        s = _make_stack(tmp_path, {"myapp": {
            "env_defaults": {"TZ": "{timezone}"},
        }})
        s.up("myapp")
        env_file = tmp_path / "stacklets" / "myapp" / ".env"
        assert env_file.exists()

        s.destroy("myapp")
        assert not env_file.exists()

    def test_up_after_destroy_is_fresh(self, tmp_path):
        """After destroy, the next up is first_run again."""
        s = _make_stack(tmp_path, {"myapp": {}})
        s.up("myapp")
        s.destroy("myapp")
        result = s.up("myapp")
        assert result["first_run"] is True
