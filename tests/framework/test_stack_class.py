"""Tests for the Stack class — the core framework object.

Stack wraps all state (config, secrets, stacklet discovery) in a single
object. No globals, no side effects on import. Fully testable.

These tests define the target interface. They drive the decomposition
of the 3000-line stack script into a clean class.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))


@pytest.fixture(autouse=True)
def isolated_env():
    """Override the conftest's isolated_env — Stack tests manage their own state."""
    yield


class TestStackInit:
    """Stack is created with explicit paths — no global state."""

    def test_creates_with_paths(self, tmp_path):
        from stack import Stack
        s = Stack(root=tmp_path, data=tmp_path / "data")
        assert s.root == tmp_path
        assert s.data == tmp_path / "data"

    def test_loads_config_from_root(self, tmp_path):
        (tmp_path / "stack.toml").write_text("""
[core]
timezone = "America/New_York"
""")
        from stack import Stack
        s = Stack(root=tmp_path, data=tmp_path / "data")
        assert s.config.get("core", {}).get("timezone") == "America/New_York"

    def test_missing_config_gives_empty_dict(self, tmp_path):
        from stack import Stack
        s = Stack(root=tmp_path, data=tmp_path / "data")
        assert s.config == {}


class TestStackDiscover:
    """Stack discovers stacklets from the stacklets/ directory."""

    def test_discovers_stacklets(self, tmp_path):
        _create_stacklet(tmp_path, "myapp", name="My App", category="test")

        from stack import Stack
        s = Stack(root=tmp_path, data=tmp_path / "data")
        stacklets = s.discover()
        assert "myapp" in {st["id"] for st in stacklets}

    def test_reads_manifest(self, tmp_path):
        _create_stacklet(tmp_path, "myapp", name="My App",
                         description="A test app", version="1.0.0")

        from stack import Stack
        s = Stack(root=tmp_path, data=tmp_path / "data")
        stacklets = {st["id"]: st for st in s.discover()}
        app = stacklets["myapp"]
        assert app["name"] == "My App"
        assert app["description"] == "A test app"
        assert app["version"] == "1.0.0"

    def test_empty_stacklets_dir(self, tmp_path):
        (tmp_path / "stacklets").mkdir()

        from stack import Stack
        s = Stack(root=tmp_path, data=tmp_path / "data")
        assert s.discover() == []


class TestStackEnv:
    """Stack renders env vars from templates + secrets."""

    def test_renders_template_vars(self, tmp_path):
        (tmp_path / "stack.toml").write_text("""
[core]
timezone = "Europe/Berlin"
data_dir = "/tmp/test-data"
""")
        _create_stacklet(tmp_path, "myapp", env_defaults={
            "TZ": "{timezone}",
            "DATA": "{data_dir}/myapp",
        })

        from stack import Stack
        s = Stack(root=tmp_path, data=Path("/tmp/test-data"))
        env = s.env("myapp")
        assert env["TZ"] == "Europe/Berlin"
        assert env["DATA"] == "/tmp/test-data/myapp"

    def test_missing_var_resolves_to_empty(self, tmp_path):
        _create_stacklet(tmp_path, "myapp", env_defaults={
            "TOKEN": "{nonexistent_var}",
        })

        from stack import Stack
        s = Stack(root=tmp_path, data=tmp_path / "data")
        env = s.env("myapp")
        assert env["TOKEN"] == ""


class TestStackInstanceDir:
    """Stack can separate where stacklets/ live (root) from where config
    and state live (instance_dir). The two coincide by default. Splitting
    them lets one repo power multiple stack instances — dev, test, sandbox."""

    def test_defaults_instance_dir_to_root(self, tmp_path):
        from stack import Stack
        s = Stack(root=tmp_path, data=tmp_path / "data")
        assert s.instance_dir == tmp_path

    def test_config_read_from_instance_dir_not_root(self, tmp_path):
        repo = tmp_path / "repo"
        instance = tmp_path / "instance"
        repo.mkdir()
        instance.mkdir()

        (repo / "stack.toml").write_text('[core]\ntimezone = "UTC"\n')
        (instance / "stack.toml").write_text('[core]\ntimezone = "Europe/Berlin"\n')

        from stack import Stack
        s = Stack(root=repo, data=tmp_path / "data", instance_dir=instance)
        assert s.config["core"]["timezone"] == "Europe/Berlin"

    def test_users_read_from_instance_dir(self, tmp_path):
        repo = tmp_path / "repo"
        instance = tmp_path / "instance"
        repo.mkdir()
        instance.mkdir()

        (repo / "users.toml").write_text(
            '[[users]]\nid = "wrong"\nname = "Wrong"\nrole = "admin"\n'
        )
        (instance / "users.toml").write_text(
            '[[users]]\nid = "homer"\nname = "Homer"\nrole = "admin"\n'
        )

        from stack.users import load_users
        from stack import Stack
        s = Stack(root=repo, data=tmp_path / "data", instance_dir=instance)
        assert load_users(s.instance_dir)[0]["id"] == "homer"

    def test_secrets_live_in_instance_dir(self, tmp_path):
        repo = tmp_path / "repo"
        instance = tmp_path / "instance"
        repo.mkdir()
        instance.mkdir()

        from stack import Stack
        s = Stack(root=repo, data=tmp_path / "data", instance_dir=instance)
        s.secrets.set("myapp", "PASSWORD", "value")
        assert (instance / ".stack" / "secrets.toml").exists()
        assert not (repo / ".stack" / "secrets.toml").exists()

    def test_stacklet_discovery_still_uses_root(self, tmp_path):
        repo = tmp_path / "repo"
        instance = tmp_path / "instance"
        repo.mkdir()
        instance.mkdir()
        _create_stacklet(repo, "myapp", name="My App")

        from stack import Stack
        s = Stack(root=repo, data=tmp_path / "data", instance_dir=instance)
        assert "myapp" in {st["id"] for st in s.discover()}

    def test_setup_marker_lives_in_instance_dir(self, tmp_path):
        repo = tmp_path / "repo"
        instance = tmp_path / "instance"
        repo.mkdir()
        instance.mkdir()
        (instance / ".stack").mkdir()

        from stack import Stack
        s = Stack(root=repo, data=tmp_path / "data", instance_dir=instance)
        marker = s._setup_done_marker("myapp")
        assert marker.parent == instance / ".stack"


class TestFindInstanceDir:
    """find_instance_dir resolves STACK_DIR env var, falls back to repo root."""

    def test_returns_repo_root_without_env(self, tmp_path, monkeypatch):
        (tmp_path / "stacklets").mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("STACK_DIR", raising=False)

        from stack.cli import find_instance_dir
        assert find_instance_dir() == tmp_path

    def test_returns_stack_dir_env_when_set(self, tmp_path, monkeypatch):
        (tmp_path / "stacklets").mkdir()
        instance = tmp_path / "alt-instance"
        instance.mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("STACK_DIR", str(instance))

        from stack.cli import find_instance_dir
        assert find_instance_dir() == instance.resolve()

    def test_expands_user_in_stack_dir(self, tmp_path, monkeypatch):
        (tmp_path / "stacklets").mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("STACK_DIR", "~")

        from stack.cli import find_instance_dir
        assert find_instance_dir() == Path("~").expanduser().resolve()

    def test_returns_none_when_stack_dir_does_not_exist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STACK_DIR", str(tmp_path / "does-not-exist"))

        from stack.cli import find_instance_dir
        assert find_instance_dir() is None


# ── Helpers ───────────────────────────────────────────────────────────────

def _create_stacklet(root, sid, name=None, description="", version="0.1.0",
                     category="test", env_defaults=None):
    """Create a minimal stacklet in root/stacklets/{sid}/."""
    stacklet_dir = root / "stacklets" / sid
    stacklet_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        f'id = "{sid}"',
        f'name = "{name or sid}"',
        f'description = "{description}"',
        f'version = "{version}"',
        f'category = "{category}"',
    ]

    if env_defaults:
        lines.append("")
        lines.append("[env.defaults]")
        for k, v in env_defaults.items():
            lines.append(f'{k} = "{v}"')

    (stacklet_dir / "stacklet.toml").write_text("\n".join(lines) + "\n")
