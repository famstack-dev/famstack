"""Shared fixtures for framework tests.

Tests use Stack directly — no subprocess calls, no symlinks.
Each test gets an isolated Stack instance with its own tmp_path.
"""

import shutil
import sys
from pathlib import Path

import pytest

# Ensure lib/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir():
    """Path to fixture stacklet definitions."""
    return FIXTURES_DIR


@pytest.fixture
def make_stack(tmp_path):
    """Factory fixture: create a Stack with test config and optional stacklets.

    Usage:
        stck = make_stack()                        # bare stack
        stck = make_stack(stacklets=["basic"])      # with fixture stacklets
        stck = make_stack(config="[ai]\\nkey=val")  # custom config
    """
    from stack import Stack
    from stack.output import CollectorOutput

    def _factory(stacklets=None, config_extra="", output=None):
        # Write test config
        (tmp_path / "stack.toml").write_text(f"""
[core]
domain = ""
data_dir = "{tmp_path / 'data'}"
timezone = "Europe/Berlin"

[ai]
openai_url = "http://localhost:8000/v1"
openai_key = "test-key"
whisper_url = "http://localhost:42062/v1"
language = "en"
default = "test-model"

[messages]
server_name = "testserver"
{config_extra}
""")

        (tmp_path / "users.toml").write_text("""
[[users]]
name     = "Test Admin"
email    = "admin@test.local"
password = "testpass"
role     = "admin"
""")

        # Runtime state
        stack_dir = tmp_path / ".stack"
        stack_dir.mkdir(exist_ok=True)
        (stack_dir / "secrets.toml").write_text(
            'global__ADMIN_PASSWORD = "testpass"\n')

        # Copy fixture stacklets into the test's stacklets/ dir
        stacklets_dir = tmp_path / "stacklets"
        stacklets_dir.mkdir(exist_ok=True)
        for name in (stacklets or []):
            src = FIXTURES_DIR / name
            if src.exists():
                shutil.copytree(src, stacklets_dir / name)

        out = output or CollectorOutput()
        return Stack(root=tmp_path, data=tmp_path / "data", output=out)

    return _factory


# ── Legacy fixtures for tests that still call the CLI as subprocess ────────
# These will be removed as tests migrate to direct Stack usage.

REPO_ROOT = Path(__file__).parent.parent.parent
STACK_DIR = REPO_ROOT / ".stack"


def _run_stack(*args) -> dict:
    """Run the stack CLI as subprocess. Legacy — prefer make_stack()."""
    import json
    import subprocess
    cmd = [sys.executable, "-m", "stack.cli"] + list(args)
    env = {**__import__("os").environ, "PYTHONPATH": str(REPO_ROOT / "lib")}
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                            cwd=str(REPO_ROOT), env=env)
    for output in [result.stdout, result.stderr]:
        try:
            data = json.loads(output)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            continue
    return {"_raw": result.stdout, "_stderr": result.stderr,
            "_code": result.returncode}


@pytest.fixture
def stack():
    """Legacy: CLI runner for tests that need subprocess calls."""
    return _run_stack


@pytest.fixture
def stack_state_dir():
    """Legacy: path to real .stack/ dir."""
    return STACK_DIR
