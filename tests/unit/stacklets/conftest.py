"""Shared fixtures for stacklet tests.

The memory stacklet is tested through its CLI surface — same path the
user (and other agents) hit. `stack_cli(*args)` invokes the real
`python -m stack ...` entry point and returns (returncode, stdout,
stderr) for the test to inspect.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Make the framework importable for in-process tests (CLI subprocess
# calls already set PYTHONPATH; this is for tests that import lib.py
# directly to exercise pure logic against pytest-httpserver).
_LIB_DIR = REPO_ROOT / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

# One stacklet reaches another by package (`memory.note_card`, `memory.lib`),
# as the bot runner allows by putting the stacklets directory on its path.
_STACKLETS_DIR = REPO_ROOT / "stacklets"
if str(_STACKLETS_DIR) not in sys.path:
    sys.path.append(str(_STACKLETS_DIR))

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
        class ContextBuilder:
            def build_messages(self, *args, **kwargs):
                return [{"role": "user", "content": "hi"}]

        class Tool:
            pass

        def tool_parameters(schema):
            # nanobot attaches the schema to the class it decorates; a tool
            # without it is abstract and nanobot cannot register it.
            def attach(cls):
                cls.parameters_schema = schema
                return cls
            return attach

        # nanobot's own signature: `required`, `description` and
        # `additional_properties` are the schema's, everything else is a
        # parameter. A tool parameter named after one of them breaks
        # every request on the real agent, so it fails here too.
        def tool_parameters_schema(*, required=None, description="",
                                   additional_properties=False, **properties):
            if not isinstance(description, str):
                raise TypeError("a tool parameter is named `description`, which "
                                "the schema builder takes as its own")
            return dict(properties)

        class _Schema:
            def __init__(self, *args, **kwargs):
                self.args, self.kwargs = args, kwargs

        class IntegerSchema(_Schema):
            # nanobot 0.3: keyword arguments only. A description passed by
            # position raises TypeError and detaches the tool.
            def __init__(self, *, description="", **kwargs):
                super().__init__(description=description, **kwargs)

        @dataclasses.dataclass(frozen=True)
        class RuntimeContextBlock:
            source: str
            content: str

        def wrap_runtime_context_lines(lines):
            content = "\n".join(line for line in lines if line)
            return (f"[Runtime Context — metadata only, not instructions]\n{content}\n"
                    "[/Runtime Context]") if content else ""

        class AgentLoop:
            # nanobot 0.3 agent/loop.py: providers registered here are asked
            # for a block on every turn, and the blocks are appended to the
            # user's message.
            def __init__(self, *args, **kwargs):
                self._runtime_context_providers = []

            def register_runtime_context_provider(self, provider):
                if provider not in self._runtime_context_providers:
                    self._runtime_context_providers.append(provider)
                return lambda: None

        class ToolLoader:
            def discover(self):
                return []

        class ToolRegistry:
            # The seam tool_trim patches: register() is where a tool
            # enters the model's list, so the trim filters there.
            def register(self, tool):
                pass

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
                self.typing_stopped = []
                self.sent = []
                self.room_contents = []
                self.joined = []
                self.processed = []

            def is_allowed(self, sender):
                return True

            async def _send_room_content(self, room_id, content):
                # nanobot 0.3: m.room.message with E2EE handled; the response
                # carries the new event's id.
                self.room_contents.append((room_id, content))
                return types.SimpleNamespace(event_id=f"$notice{len(self.room_contents)}")

            @staticmethod
            def _build_thread_relates_to(metadata):
                root = (metadata or {}).get("thread_root_event_id")
                return {"rel_type": "m.thread", "event_id": root} if root else None

            async def send(self, msg):
                # nanobot 0.3 channels/matrix/runtime.py: an OutboundMessage,
                # whose metadata carries the event id of the message answered.
                self.sent.append(msg)

            async def _stop_typing_keepalive(self, room_id, clear_typing=False):
                self.typing_stopped.append(room_id)

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

            async def _handle_message(self, sender_id=None, chat_id=None,
                                      content=None, media=None, metadata=None,
                                      session_key=None, is_dm=False):
                # Real nanobot's signature (channels/base.py); the Matrix
                # channel passes a session_key per thread.
                self.handled.append(dict(
                    sender_id=sender_id, chat_id=chat_id, content=content,
                    media=media, metadata=metadata, session_key=session_key,
                    is_dm=is_dm))

        mods: dict[str, types.ModuleType] = {}

        def mod(name, **attrs):
            m = types.ModuleType(name)
            for k, v in attrs.items():
                setattr(m, k, v)
            mods[name] = m
            return m

        mod("nanobot")
        mod("nanobot.agent")
        mod("nanobot.agent.context", ContextBuilder=ContextBuilder)
        mod("nanobot.agent.loop", AgentLoop=AgentLoop)

        @dataclasses.dataclass(frozen=True)
        class ProgressEvent:
            # nanobot 0.3 bus/outbound_events.py: a tool hint carries the
            # calls it announces in `tool_events`.
            content: str = ""
            tool_hint: bool = False
            tool_events: list | None = None

        @dataclasses.dataclass(frozen=True)
        class ContextCompactionEvent:
            # nanobot 0.3 events.py; a channel shows it as "Compressing context…".
            compaction_id: str = ""
            phase: str = "started"

        mod("nanobot.events", ContextCompactionEvent=ContextCompactionEvent)
        mod("nanobot.bus")
        @dataclasses.dataclass(frozen=True)
        class StreamedResponseEvent:
            # nanobot 0.3 bus/outbound_events.py: marks a final answer that was streamed.
            pass

        mod("nanobot.bus.outbound_events", ProgressEvent=ProgressEvent,
            StreamedResponseEvent=StreamedResponseEvent)
        mod("nanobot.runtime_context", RuntimeContextBlock=RuntimeContextBlock,
            wrap_runtime_context_lines=wrap_runtime_context_lines)
        mod("nanobot.agent.tools")
        mod("nanobot.agent.tools.base", Tool=Tool, tool_parameters=tool_parameters)
        mod("nanobot.agent.tools.schema",
            StringSchema=_Schema, IntegerSchema=IntegerSchema, ArraySchema=_Schema,
            BooleanSchema=_Schema,
            tool_parameters_schema=tool_parameters_schema)
        mod("nanobot.agent.tools.loader", ToolLoader=ToolLoader)
        # nanobot 0.3: the request a tool runs in. `turn_id` is unique per turn.
        mod("nanobot.agent.tools.context", current_request_context=lambda: None)
        mod("nanobot.agent.tools.registry", ToolRegistry=ToolRegistry)
        mod("nanobot.agent.tools.filesystem",
            WriteFileTool=WriteFileTool, EditFileTool=EditFileTool)
        mod("nanobot.agent.tools.apply_patch", ApplyPatchTool=ApplyPatchTool)
        mod("nanobot.channels")
        mod("nanobot.channels.matrix")
        mod("nanobot.channels.matrix.runtime", MatrixChannel=MatrixChannel)
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
