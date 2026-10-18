"""What `stack <id>` and `stack <id> <cmd> --help` say about a stacklet's commands.

Every command of every stacklet is a CLI plugin, and its help comes from
the plugin itself: its `HELP` line in the command list, its docstring as
the usage. Agents learn the commands this way, so the help is a contract
like the commands' exit codes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _stack(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "stack", *args], capture_output=True, text=True,
                          cwd=str(REPO_ROOT), env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "lib")},
                          timeout=60)


def test_a_stacklet_lists_its_commands_with_their_one_line_help():
    out = _stack("memory", "--help").stdout

    assert "list" in out and "Show and change the family's lists" in out
    assert "capture" in out and "File a note, link, or image" in out


def test_a_commands_help_is_its_usage_and_examples():
    out = _stack("memory", "history", "--help").stdout

    assert "stack memory history --by marge" in out
