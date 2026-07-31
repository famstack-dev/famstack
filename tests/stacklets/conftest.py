"""Shared fixtures for stacklet tests.

The memory stacklet is tested through its CLI surface — same path the
user (and other agents) hit. `stack_cli(*args)` invokes the real
`python -m stack ...` entry point and returns (returncode, stdout,
stderr) for the test to inspect.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Make the framework importable for in-process tests (CLI subprocess
# calls already set PYTHONPATH; this is for tests that import lib.py
# directly to exercise pure logic against pytest-httpserver).
_LIB_DIR = REPO_ROOT / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

# Stacklet runtime modules (e.g. the agent's nanobot shims) live on the
# container's PYTHONPATH, not on any package path a test can reach. Register
# them here once, by convention, so a test importing one is a plain import
# instead of a per-file sys.path hack with a `noqa: E402` chaser.
for _runtime_dir in sorted(REPO_ROOT.glob("stacklets/*/runtime")):
    if str(_runtime_dir) not in sys.path:
        sys.path.insert(0, str(_runtime_dir))


@pytest.fixture
def stack_cli():
    """Run a `stack ...` command and return (returncode, stdout, stderr).

    Uses the real CLI entry point, real stacklets, real seeds — no
    mocks, no fixtures intercepting reads. Matches how `stack` is
    invoked by the user and by other agents.
    """

    def _run(*args, timeout: int = 30) -> tuple[int, str, str]:
        cmd = [sys.executable, "-m", "stack", *args]
        env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "lib")}
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, cwd=str(REPO_ROOT), env=env,
        )
        return result.returncode, result.stdout, result.stderr

    return _run
