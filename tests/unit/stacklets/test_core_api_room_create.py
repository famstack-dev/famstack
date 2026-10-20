"""The core API's `room-create`: Stacker's way to `stack messages room create`.

The JSON surface is for trusted core tools. It runs a fixed set of
`stack` commands, and this one runs a single messages command with the
room's name as one argument, so a name such as `Topic: Garden; rm -rf`
stays a name. The `stack` binary is replaced by a script that answers
with the arguments it was given.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


@pytest.fixture
def api(tmp_path, monkeypatch):
    path = REPO_ROOT / "stacklets" / "core" / "famstack-api.py"
    loader = importlib.machinery.SourceFileLoader("famstack_api", str(path))
    spec = importlib.util.spec_from_file_location("famstack_api", path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    stack = tmp_path / "stack"
    stack.write_text('#!/usr/bin/env python3\nimport json, sys\n'
                     'print(json.dumps({"argv": sys.argv[1:]}))\n')
    stack.chmod(0o755)
    monkeypatch.setattr(module, "STACK_BIN", stack)
    return module


def test_room_create_runs_the_messages_command_with_the_name_as_one_argument(api):
    reply = api.handle_request(json.dumps({"cmd": "room-create", "name": "Topic: Garden; ls"}))

    assert reply == {"argv": ["messages", "room", "create", "Topic: Garden; ls", "--json"]}


def test_room_create_needs_a_name(api):
    reply = api.handle_request(json.dumps({"cmd": "room-create", "name": "  "}))

    assert "error" in reply
