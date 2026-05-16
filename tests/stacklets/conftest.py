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
