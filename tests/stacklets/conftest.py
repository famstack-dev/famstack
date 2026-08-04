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
import types
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
def nanobot_stub():
    """Build a stub `nanobot` module tree covering the surface we patch.

    Lives here rather than in one test file because two suites need it:
    the shim tests assert the patches attach, and the vault-tool tests
    drive the tools those patches register. Deliberately hand-built
    rather than mocked -- every name is a symbol `sitecustomize.py`
    pins, so the stub doubles as a written record of what we depend on,
    and a Mock would satisfy any attribute and prove nothing.

    Returns a callable so a test can build a fresh tree per case.
    """

    def _build() -> dict[str, types.ModuleType]:
        def runtime_lines(state, msg, workspace, *, skip=False):
            return ["stock line"]

        class ContextBuilder:
            def build_messages(self, *args, **kwargs):
                return [{"role": "user", "content": "hi"}]

        class Tool:
            pass

        def tool_parameters(schema):
            return lambda cls: cls

        def tool_parameters_schema(**kwargs):
            return dict(kwargs)

        class _Schema:
            def __init__(self, *args, **kwargs):
                self.args, self.kwargs = args, kwargs

        class ToolLoader:
            def discover(self):
                return []

        class GrepTool:
            async def execute(self, *args, **kwargs):
                return "stock grep"

        # The three write tools, `async def` exactly as upstream declares them.
        # That detail is the contract, not decoration: nanobot's tool loop
        # awaits the result, so a shim that replaces one with a sync function
        # returns a str into an `await` and the call dies. Keeping the stub
        # async is what makes the test able to notice.
        class WriteFileTool:
            async def execute(self, path=None, content=None, **kwargs):
                return f"stock write {path}"

        class EditFileTool:
            async def execute(self, path=None, **kwargs):
                return f"stock edit {path}"

        class ApplyPatchTool:
            async def execute(self, edits=None, **kwargs):
                return f"stock patch {edits}"

        class MatrixChannel:
            def __init__(self):
                self.client = types.SimpleNamespace(rooms={})
                self.config = types.SimpleNamespace(
                    user_id="@stacky-bot:home.local", group_policy="mention",
                )
                self.handled = []
                self.joined = []
                self.processed = []

            def _is_bot_mentioned(self, event):
                # Stock nanobot: only an autocompleted pill counts.
                return getattr(event, "pill_mention", False)

            def _should_process_message(self, room, event):
                # Stock nanobot under `groupPolicy: mention`, which is what
                # the agent ships with: the mention gate decides.
                return self._is_bot_mentioned(event)

            async def _on_message(self, room, event):
                # Stock nanobot: gate, then hand the text to the agent.
                if self._should_process_message(room, event):
                    self.processed.append(event)

            async def _on_media_message(self, room, event):
                if self._should_process_message(room, event):
                    self.processed.append(event)

            async def _on_room_invite(self, room, event):
                # Stock nanobot: join, say nothing.
                self.joined.append(room.room_id)

            async def _handle_message(self, **kwargs):
                self.handled.append(kwargs)

        mods: dict[str, types.ModuleType] = {}

        def mod(name, **attrs):
            m = types.ModuleType(name)
            for k, v in attrs.items():
                setattr(m, k, v)
            mods[name] = m
            return m

        mod("nanobot")
        mod("nanobot.agent")
        mod("nanobot.agent.context",
            runtime_lines=runtime_lines, ContextBuilder=ContextBuilder)
        mod("nanobot.agent.tools")
        mod("nanobot.agent.tools.base", Tool=Tool, tool_parameters=tool_parameters)
        mod("nanobot.agent.tools.schema",
            StringSchema=_Schema, IntegerSchema=_Schema,
            tool_parameters_schema=tool_parameters_schema)
        mod("nanobot.agent.tools.loader", ToolLoader=ToolLoader)
        mod("nanobot.agent.tools.search", GrepTool=GrepTool)
        mod("nanobot.agent.tools.filesystem",
            WriteFileTool=WriteFileTool, EditFileTool=EditFileTool)
        mod("nanobot.agent.tools.apply_patch", ApplyPatchTool=ApplyPatchTool)
        mod("nanobot.channels")
        mod("nanobot.channels.matrix", MatrixChannel=MatrixChannel)
        return mods

    return _build


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
