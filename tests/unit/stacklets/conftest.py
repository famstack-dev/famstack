"""Shared fixtures for stacklet tests.

Memory's tests and theirs (`stack_cli`, `forgejo_accounts`) are in
`tests/memory/unit/`.
"""

from __future__ import annotations

import dataclasses
import importlib
import os
import re
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

# The agent's container mounts single framework files into `/app/shared`
# under their own names (compose); here they are the same modules of `lib/stack`.
sys.modules.setdefault("agent_audience", importlib.import_module("stack.agent_audience"))
sys.modules.setdefault("agent_confirm", importlib.import_module("stack.agent_confirm"))


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

            # The turn stage own_chat records a message's files at.
            async def _restore_turn(self, ctx):
                pass

        class TurnKind:
            USER = "user"
            SYSTEM = "system"

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

        class ExecTool:
            # nanobot 0.3.5 agent/tools/shell.py: `execute` builds the
            # command's environment with `_build_env`, inside the tool call,
            # from a few basics and the keys config.json allows.
            allowed_env_keys = ["PATH", "STACK_API_TOKEN"]

            def _build_env(self):
                env = {"HOME": os.environ.get("HOME", "/tmp"), "PYTHONUNBUFFERED": "1"}
                for key in self.allowed_env_keys:
                    if (val := os.environ.get(key)) is not None:
                        env[key] = val
                return env

        # own_chat's symbols (nanobot 0.3.5): the store consolidation writes
        # the shared history through, the error a tool answers with, and the
        # path check of the file and message tools.
        class MemoryStore:
            def append_history(self, entry, *, max_chars=None, session_key=None):
                return 1

        class ToolResult(str):
            @classmethod
            def error(cls, content):
                return cls(content)

        class WorkspaceBoundaryError(PermissionError):
            pass

        def resolve_workspace_path(path, workspace=None, allowed_dir=None,
                                   extra_allowed_dirs=None, extra_allowed_files=None,
                                   include_media_dir=True):
            return path

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
                self.downloaded = []

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

            def _is_direct_room(self, room):
                # nanobot 0.3: a room of two is a direct chat.
                count = getattr(room, "member_count", None)
                return isinstance(count, int) and count <= 2

            def _is_pre_startup_event(self, event):
                # nanobot 0.3: sync replays the timeline on a restart; an
                # event older than the process is not handled again.
                return getattr(event, "pre_startup", False)

            def _should_process_message(self, room, event):
                # Stock nanobot under `groupPolicy: mention`, which is what
                # the agent ships with: a direct chat passes, otherwise the
                # mention gate decides.
                if self._is_direct_room(room):
                    return True
                return self._is_bot_mentioned(event)

            async def _fetch_media_attachment(self, room, event):
                # nanobot 0.3: downloads the file into its media dir and
                # returns the attachment and the marker for the agent's text.
                event_id = getattr(event, "event_id", "")
                self.downloaded.append(event_id)
                path = f"/media/matrix/{event_id.lstrip('$')}.jpg"
                msgtype = event.source["content"]["msgtype"]
                atype = {"m.image": "image", "m.audio": "audio"}.get(msgtype, "file")
                return ({"type": atype, "event_id": event_id, "path": path},
                        f"[attachment: {path}]")

            async def _on_message(self, room, event):
                # Stock nanobot: gate, then hand the text to the agent.
                if (event.sender == self.config.user_id
                        or self._is_pre_startup_event(event)
                        or not self._should_process_message(room, event)):
                    return
                self.processed.append(event)
                await self._handle_message(
                    sender_id=event.sender, chat_id=getattr(room, "room_id", ""),
                    content=getattr(event, "body", ""),
                    metadata={"event_id": getattr(event, "event_id", "")},
                    is_dm=self._is_direct_room(room))

            async def _on_media_message(self, room, event):
                # Stock nanobot: gate, download, then hand the caption and
                # the attachment's marker to the agent, the file as media.
                if (event.sender == self.config.user_id
                        or self._is_pre_startup_event(event)
                        or not self._should_process_message(room, event)):
                    return
                self.processed.append(event)
                attachment, marker = await self._fetch_media_attachment(room, event)
                parts = [event.body.strip()] if event.body.strip() else []
                parts.append(marker)
                await self._handle_message(
                    sender_id=event.sender, chat_id=getattr(room, "room_id", ""),
                    content="\n".join(parts), media=[attachment["path"]],
                    metadata={"event_id": getattr(event, "event_id", ""),
                              "attachments": [attachment]},
                    is_dm=self._is_direct_room(room))

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
        mod("nanobot.agent.loop", AgentLoop=AgentLoop, TurnKind=TurnKind)

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
        mod("nanobot.agent.tools.base", Tool=Tool, ToolResult=ToolResult,
            tool_parameters=tool_parameters)
        mod("nanobot.agent.memory", MemoryStore=MemoryStore)
        mod("nanobot.agent.tools.path_utils", resolve_workspace_path=resolve_workspace_path)
        mod("nanobot.agent.tools.message", resolve_workspace_path=resolve_workspace_path)
        mod("nanobot.security")
        mod("nanobot.security.workspace_policy", WorkspaceBoundaryError=WorkspaceBoundaryError)
        mod("nanobot.agent.tools.schema",
            StringSchema=_Schema, IntegerSchema=IntegerSchema, ArraySchema=_Schema,
            BooleanSchema=_Schema,
            tool_parameters_schema=tool_parameters_schema)
        mod("nanobot.agent.tools.loader", ToolLoader=ToolLoader)
        # nanobot 0.3: the request a tool runs in. `turn_id` is unique per turn.
        mod("nanobot.agent.tools.context", current_request_context=lambda: None)
        mod("nanobot.agent.tools.registry", ToolRegistry=ToolRegistry)
        mod("nanobot.agent.tools.filesystem",
            WriteFileTool=WriteFileTool, EditFileTool=EditFileTool,
            resolve_workspace_path=resolve_workspace_path)
        mod("nanobot.agent.tools.apply_patch", ApplyPatchTool=ApplyPatchTool)
        mod("nanobot.agent.tools.shell", ExecTool=ExecTool)
        mod("nanobot.channels")
        mod("nanobot.channels.matrix")
        mod("nanobot.channels.matrix.runtime", MatrixChannel=MatrixChannel)
        return mods

    return _build


# The agent's runtime modules `sitecustomize` imports, purged before and
# after each load so the shims re-run instead of returning a module that is
# already patched.
SHIMMED_MODULES = ("sitecustomize", "brief", "state_log",
                   "memory_tool", "person_tool", "history_tool",
                   "name_trigger", "thread_trigger", "join_greeting", "vault_write",
                   "list_tool", "tool_trim", "voice_trigger", "open_question",
                   "status_trace", "turn", "photo_before", "room_people", "room_reads",
                   "own_chat")


@pytest.fixture
def nanobot(monkeypatch, nanobot_stub):
    """Install a stub nanobot and import `sitecustomize` against it.

    Returns a callable so a test can drop a symbol first and watch what
    survives. A second call is a restart: the shims attach again to a fresh
    stub and keep nothing from the first.
    """
    def _load(drop: str | None = None):
        mods = nanobot_stub()
        if drop:
            # A module's attribute, or a class's (`module.Class.method`).
            owner_name, _, attr = drop.rpartition(".")
            module_name, _, class_name = owner_name.rpartition(".")
            owner = (mods[owner_name] if owner_name in mods
                     else getattr(mods[module_name], class_name))
            delattr(owner, attr)
        for name, module in mods.items():
            monkeypatch.setitem(sys.modules, name, module)
        for name in SHIMMED_MODULES:
            monkeypatch.delitem(sys.modules, name, raising=False)
        # The container mounts lib/stack/name_trigger.py as `name_trigger`.
        monkeypatch.setitem(sys.modules, "name_trigger",
                            importlib.import_module("stack.name_trigger"))
        importlib.import_module("sitecustomize")
        return mods

    yield _load

    for name in SHIMMED_MODULES:
        sys.modules.pop(name, None)


# ── Another server on the IPv6 loopback ───────────────────────────────────


@pytest.fixture
def other_server_on_ipv6(httpserver):
    """Another process on `[::1]` at the stand-in's port, answering 404 to everything.

    The stand-in listens on 127.0.0.1 only, so the same port number on ::1
    is free for any other process: OrbStack and Node dev servers listen
    there on this kind of Mac, and so do the other workers of a parallel
    run. A client that asks for `localhost` tries ::1 first and reaches it.
    A command that reaches the stack's own service through
    `stack.own_service` sends nothing here, which `other.log` shows.
    """
    from pytest_httpserver import HTTPServer
    other = HTTPServer(host="::1", port=httpserver.port)
    other.expect_request(re.compile(".*")).respond_with_data("not the stack's service", status=404)
    try:
        other.start()
    except OSError as e:
        pytest.skip(f"cannot listen on [::1]:{httpserver.port}: {e}")
    yield other
    other.stop()


# ── Stacky in a room ──────────────────────────────────────────────────────
#
# Stacky as a person meets it, reading and writing the memory through its
# real `stack` shim and the host API's own checks (`stacky_in_a_room`). A
# test module gives the `memory` fixture its own seed.


@pytest.fixture(scope="module")
def api():
    """The host API module whose allowlist gates every command the agent runs."""
    from tests.unit.stacklets.stacky_in_a_room import REPO_ROOT, load_from_path
    return load_from_path("famstack_api_for_the_room",
                          REPO_ROOT / "stacklets" / "core" / "famstack-api.py")


@pytest.fixture(scope="module")
def shim():
    """The container-side `stack` shim, the agent's only route to the CLI."""
    from tests.unit.stacklets.stacky_in_a_room import REPO_ROOT, load_from_path
    return load_from_path("stack_shim_for_the_room",
                          REPO_ROOT / "stacklets" / "agent" / "client" / "stack")


@pytest.fixture
def runtime(nanobot):
    """Stacky's runtime: the stub nanobot with every shim of `sitecustomize` on it."""
    return nanobot()


@pytest.fixture
def stacky(memory, api, shim, runtime, tmp_path, monkeypatch):
    """Stacky, with a real `stack` shim on `PATH` that talks to the host API."""
    from tests.unit.stacklets.stacky_in_a_room import stacky_serving
    with stacky_serving(memory, api, shim, runtime, tmp_path / "bin", monkeypatch) as stacky:
        yield stacky
