"""`--json` on a stacklet command prints what the command returns.

The plugin contract (docs/agent/dev.md) is that a stacklet command returns a
dict and the framework decides between JSON and text. Scripts and agents drive
the stack through that answer: `stack messages send ... --json` has to hand
back the event id it posted. So with `--json` the returned dict is the only
thing on stdout, whatever the command prints for people goes to stderr, and
the flag itself never reaches the command as an argument.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

HELLO = '''
HELP = "Say hello"
import sys

def run(args, stacklet, config):
    print("hello, for people")
    if "fail" in args:
        return {"error": "asked to fail"}
    return {"ok": True, "args": list(args), "argv": sys.argv[3:]}
'''


def _instance(tmp_path: Path) -> dict:
    extensions = tmp_path / "extensions"
    cli = extensions / "echo" / "cli"
    cli.mkdir(parents=True)
    (extensions / "echo" / "stacklet.toml").write_text(
        'id = "echo"\nname = "Echo"\nversion = "0.1.0"\ncategory = "test"\n')
    (cli / "hello.py").write_text(HELLO)
    (tmp_path / "stack.toml").write_text(
        f'[core]\nname = "stack"\ndata_dir = "{tmp_path / "data"}"\n'
        f'extension_dirs = ["{extensions}"]\n')
    return {**os.environ, "STACK_DIR": str(tmp_path)}


def _stack(env, *args):
    return subprocess.run([str(REPO / "stack"), *args], env=env,
                          capture_output=True, text=True, timeout=60)


def test_json_prints_the_returned_answer_and_nothing_else(tmp_path):
    result = _stack(_instance(tmp_path), "echo", "hello", "world", "--json")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"ok": True, "args": ["world"], "argv": ["world"]}
    assert "hello, for people" in result.stderr


def test_without_json_the_command_speaks_for_itself(tmp_path):
    result = _stack(_instance(tmp_path), "echo", "hello", "world")

    assert result.returncode == 0, result.stderr
    assert "hello, for people" in result.stdout
    assert "{" not in result.stdout


def test_an_error_is_json_too_and_fails_the_command(tmp_path):
    result = _stack(_instance(tmp_path), "echo", "hello", "fail", "--json")

    assert result.returncode == 1
    assert json.loads(result.stdout) == {"error": "asked to fail"}
