"""End-to-end: full lifecycle with Docker containers.

The critical pipeline:
  stack.toml → template vars → [env.defaults] → .env → docker compose → container env

Uses a session-scoped test stack with the test stacklet (Alpine).
Container is reused across tests for speed (~30s total instead of ~3min).
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Skip entire module if Docker is not available
try:
    subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
    HAS_DOCKER = True
except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
    HAS_DOCKER = False

pytestmark = pytest.mark.skipif(not HAS_DOCKER, reason="Docker not available")


class IsolatedStack:
    """Test stack environment that can be reused across tests."""

    def __init__(self, root: Path, data: Path):
        self.root = root
        self.data = data
        self._original_config = (root / "stack.toml").read_text()

    def _env(self):
        return {**os.environ, "PYTHONPATH": str(self.root / "lib")}

    def run(self, *args, timeout=60) -> dict:
        """Run a stack command. Returns parsed result or raw output."""
        cmd = [sys.executable, "-m", "stack"] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=timeout, cwd=str(self.root), env=self._env())
        for output in [result.stdout, result.stderr]:
            try:
                data = json.loads(output)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, ValueError):
                continue
        return {"ok": result.returncode == 0,
                "_raw": result.stdout, "_stderr": result.stderr,
                "_code": result.returncode}

    def run_pretty(self, *args, timeout=60) -> tuple[int, str, str]:
        """Run a stack command. Returns (exit_code, stdout, stderr)."""
        cmd = [sys.executable, "-m", "stack"] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=timeout, cwd=str(self.root), env=self._env())
        return result.returncode, result.stdout, result.stderr

    def update_config(self, old_value: str, new_value: str):
        """Update a value in stack.toml."""
        config_path = self.root / "stack.toml"
        config = config_path.read_text()
        config_path.write_text(config.replace(old_value, new_value))

    def reset_config(self):
        """Restore original stack.toml."""
        (self.root / "stack.toml").write_text(self._original_config)

    def reset_state(self):
        """Reset state for next test (keep container, reset config)."""
        self.reset_config()
        # Clear setup-done marker so next test can do fresh install
        marker = self.root / ".stack" / "test.setup-done"
        if marker.exists():
            marker.unlink()


@pytest.fixture(scope="module")
def test_stack(tmp_path_factory):
    """Module-scoped test stack. Created once, reused across all tests."""
    tmp_path = tmp_path_factory.mktemp("stack")
    stack_root = tmp_path / "stack"
    stack_root.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Copy test stacklet from fixtures
    shutil.copytree(FIXTURES_DIR / "test", stack_root / "stacklets" / "test")

    # Symlink lib/ instead of copying (much faster)
    (stack_root / "lib").symlink_to(REPO_ROOT / "lib")

    # Write stack.toml
    (stack_root / "stack.toml").write_text(f'''
[core]
name = "teststack"
domain = ""
data_dir = "{data_dir}"
timezone = "Europe/Berlin"

[ai]
openai_url = "http://localhost:8000/v1"
openai_key = "local"
default = ""
''')

    # Write users.toml
    (stack_root / "users.toml").write_text('''
[[users]]
name = "Test Admin"
email = "admin@test.local"
password = "testpass"
role = "admin"
''')

    # Create .stack dir
    stack_dir = stack_root / ".stack"
    stack_dir.mkdir()
    (stack_dir / "secrets.toml").write_text('global__ADMIN_PASSWORD = "testpass"\n')

    # Ensure network exists
    subprocess.run(["docker", "network", "create", "stack"],
                   capture_output=True, timeout=10)

    stack = IsolatedStack(stack_root, data_dir)

    yield stack

    # Module teardown: destroy container
    stack.run("destroy", "test", "--yes")
    subprocess.run(["docker", "rm", "-f", "stack-test"],
                   capture_output=True, timeout=10)


@pytest.fixture
def isolated_stack(test_stack):
    """Per-test fixture that resets state but reuses the container."""
    yield test_stack
    test_stack.reset_state()


def docker_env(container_name: str) -> dict:
    """Read environment variables from a running container."""
    for _ in range(5):
        result = subprocess.run(
            ["docker", "exec", container_name, "env"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            break
        time.sleep(0.5)
    else:
        return {}

    env = {}
    for line in result.stdout.strip().split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    return env


def container_running(name: str, retries=5) -> bool:
    """Check if a container is running, with retries for startup lag."""
    for _ in range(retries):
        result = subprocess.run(
            ["docker", "ps", "--filter", f"name=^{name}$",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
        if name in result.stdout:
            return True
        time.sleep(0.5)
    return False


def wait_for_stop(name: str, timeout=10) -> bool:
    """Wait for a container to stop. Returns True if stopped."""
    for _ in range(timeout * 2):
        result = subprocess.run(
            ["docker", "ps", "--filter", f"name=^{name}$",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
        if name not in result.stdout:
            return True
        time.sleep(0.5)
    return False


# ── Config → Container pipeline ──────────────────────────────────────────

class TestConfigToContainer:
    """Config values from stack.toml reach the running container as env vars."""

    def test_timezone_reaches_container(self, isolated_stack):
        """TZ from stack.toml appears in the container's environment."""
        result = isolated_stack.run("up", "test")
        assert result.get("ok"), f"stack up test failed: {result}"

        env = docker_env("stack-test")
        assert env.get("TZ") == "Europe/Berlin", \
            f"Expected TZ=Europe/Berlin, got: {env.get('TZ')}"

    def test_config_change_propagates_on_restart(self, isolated_stack):
        """Changing stack.toml and re-running stack up updates the container."""
        isolated_stack.run("up", "test")
        env_before = docker_env("stack-test")
        assert env_before.get("TZ") == "Europe/Berlin"

        isolated_stack.update_config("Europe/Berlin", "America/New_York")
        isolated_stack.run("up", "test")

        env_after = docker_env("stack-test")
        assert env_after.get("TZ") == "America/New_York", \
            f"Expected America/New_York, got: {env_after.get('TZ')}"


# ── Stop / Restart ────────────────────────────────────────────────────────

class TestStopAndRestart:

    def test_down_stops_container(self, isolated_stack):
        isolated_stack.run("up", "test")
        assert container_running("stack-test")

        isolated_stack.run("down", "test")
        assert wait_for_stop("stack-test"), "Container did not stop in time"

    def test_up_after_down_brings_it_back(self, isolated_stack):
        isolated_stack.run("up", "test")
        isolated_stack.run("down", "test")
        result = isolated_stack.run("up", "test")
        assert result.get("ok"), f"up after down failed: {result}"
        assert container_running("stack-test")


# ── Destroy / Recreate ───────────────────────────────────────────────────

class TestDestroyAndRecreate:

    def test_destroy_removes_container_and_data(self, isolated_stack):
        isolated_stack.run("up", "test")
        assert container_running("stack-test")

        isolated_stack.run("destroy", "test", "--yes")
        assert wait_for_stop("stack-test"), "Container did not stop in time"
        assert not (isolated_stack.data / "test").exists()

    def test_up_after_destroy_is_first_run(self, isolated_stack):
        isolated_stack.run("up", "test")
        isolated_stack.run("destroy", "test", "--yes")

        result = isolated_stack.run("up", "test")
        assert result.get("ok"), f"up after destroy failed: {result}"
        assert container_running("stack-test")


# ── Output quality ────────────────────────────────────────────────────────

class TestOutputBehavior:
    """Verify the CLI produces clean, readable output during lifecycle ops."""

    def test_up_shows_stacklet_name(self, isolated_stack):
        """'stack up test' output includes the stacklet name."""
        code, stdout, stderr = isolated_stack.run_pretty("up", "test")
        assert code == 0
        combined = stdout + stderr
        assert "Test" in combined

    def test_up_shows_lifecycle_steps(self, isolated_stack):
        """Framework reports progress steps during up."""
        code, stdout, stderr = isolated_stack.run_pretty("up", "test")
        assert code == 0
        combined = stdout + stderr
        assert "Rendering environment" in combined
        assert "Writing .env" in combined

    def test_up_shows_success_banner(self, isolated_stack):
        """Successful up shows the result banner with URL and hints."""
        code, stdout, stderr = isolated_stack.run_pretty("up", "test")
        assert code == 0
        combined = stdout + stderr
        assert "is running" in combined
        assert "42099" in combined

    def test_up_shows_hints(self, isolated_stack):
        """Hints from stacklet.toml are rendered in the success banner."""
        code, stdout, stderr = isolated_stack.run_pretty("up", "test")
        assert code == 0
        combined = stdout + stderr
        assert "Test service running" in combined

    def test_no_garbled_output(self, isolated_stack):
        """Output should not have overlapping/garbled lines from spinners."""
        code, stdout, stderr = isolated_stack.run_pretty("up", "test")
        combined = stdout + stderr
        cr_lines = [l for l in combined.split("\n") if "\r" in l and l.strip()]
        assert not cr_lines, \
            f"Carriage returns in output (spinner leaked): {cr_lines[:3]}"

    def test_destroy_output_is_clean(self, isolated_stack):
        """Destroy output doesn't have garbled lines."""
        isolated_stack.run_pretty("up", "test")
        code, stdout, stderr = isolated_stack.run_pretty("destroy", "test", "--yes")
        assert code == 0
        combined = stdout + stderr
        assert "destroyed" in combined
        cr_lines = [l for l in combined.split("\n") if "\r" in l and l.strip()]
        assert not cr_lines, \
            f"Carriage returns in output (spinner leaked): {cr_lines[:3]}"


# ── Config propagation ───────────────────────────────────────────────────

class TestConfigPropagation:
    """Env vars from stack.toml, secrets, and users.toml reach the container."""

    def test_secret_in_env_reaches_container(self, isolated_stack):
        """A generated secret appears in the container's environment."""
        isolated_stack.run("up", "test")
        env = docker_env("stack-test")
        assert env.get("TEST_SECRET"), \
            f"TEST_SECRET not in container env. Got: {sorted(env.keys())}"

    def test_admin_username_reaches_container(self, isolated_stack):
        """Tech admin username reaches the container via template vars."""
        isolated_stack.run("up", "test")
        env = docker_env("stack-test")
        assert env.get("TEST_ADMIN") == "stackadmin", \
            f"Expected TEST_ADMIN='stackadmin', got: {env.get('TEST_ADMIN')}"

    def test_config_change_visible_on_next_env(self, isolated_stack):
        """Writing to stack.toml is picked up by the next 'stack env' command."""
        isolated_stack.run("up", "test")
        isolated_stack.update_config("Europe/Berlin", "Asia/Tokyo")

        code, stdout, stderr = isolated_stack.run_pretty("env", "test")
        assert code == 0
        assert "Asia/Tokyo" in stdout, \
            f"Expected Asia/Tokyo in env output, got: {stdout[:300]}"

    def test_config_change_reaches_container_on_restart(self, isolated_stack):
        """Changing stack.toml and re-running stack up updates the container."""
        isolated_stack.run("up", "test")
        env_before = docker_env("stack-test")
        assert env_before.get("TZ") == "Europe/Berlin"

        isolated_stack.update_config("Europe/Berlin", "America/New_York")
        isolated_stack.run("up", "test")

        env_after = docker_env("stack-test")
        assert env_after.get("TZ") == "America/New_York", \
            f"Expected America/New_York after restart, got: {env_after.get('TZ')}"

    def test_secret_change_reaches_container_on_restart(self, isolated_stack):
        """Changing a secret and re-running stack up updates the container."""
        import sys
        sys.path.insert(0, str(isolated_stack.root / "lib"))
        from stack.secrets import TomlSecretStore

        isolated_stack.run("up", "test")
        env_before = docker_env("stack-test")
        old_secret = env_before.get("TEST_SECRET")
        assert old_secret, "TEST_SECRET should exist after first up"

        # Write a new secret value directly
        secrets = TomlSecretStore(isolated_stack.root / ".stack" / "secrets.toml")
        secrets.set("test", "TEST_SECRET", "changed-value-123")

        isolated_stack.run("up", "test")

        env_after = docker_env("stack-test")
        assert env_after.get("TEST_SECRET") == "changed-value-123", \
            f"Expected 'changed-value-123' after restart, got: {env_after.get('TEST_SECRET')}"
