"""stacky_in_a_room: Stacky in one room, as a person meets it, for tests.

    stacky.in_room(Room("!homer-chat:simpson", ("homer",), "homer"))
    stacky.searches("gift")                         # the memory_search tool's answer
    stacky.execs("memory find gift")                # a line the model runs with exec
    stacky.asks_calendar(op="show", days=7)         # the calendar tool's answer

Every read and write starts a real `stack` process: the agent's
container-side shim on `PATH`, talking to `host_api`, which decides each
line with the host API's own `agent_command` and runs it on memory's test
instance (`running_memory`). The fixtures `api`, `shim`, `runtime` and
`stacky` (tests/unit/stacklets/conftest.py) build it; a test module gives
the `memory` fixture its own seed.

Shared by the tests of what Stacky may read in a room and of the dates a
person keeps for themselves, so both meet Stacky the same way.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.machinery
import importlib.util
import os
import shlex
import socket
import subprocess
import sys
import threading
import types
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from tests.loopback import serving

__all__ = ["REPO_ROOT", "RUNTIME_KEY", "Room", "Stacky", "load_from_path", "host_api",
           "run_command", "stacky_serving"]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# The key the runtime signs a room's people with, as `stack up agent` makes
# it; the host API checks each memory read against it.
RUNTIME_KEY = "runtime-audience-key-5c1e"


@dataclass(frozen=True)
class Room:
    """A room Stacky answers in: its key, who is in it, and who is asking."""

    key: str
    people: tuple[str, ...]
    asker: str
    label: str = ""


def load_from_path(name: str, path: Path):
    """Import a script by its path: `famstack-api.py` has a hyphen and the
    client shim has no suffix, so neither imports by name."""
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Stacky, as a person meets it ─────────────────────────────────────

class Stacky:
    """Stacky in one room, reading the memory as its runtime does.

        stacky = Stacky(runtime, bin_dir, port, monkeypatch)
        stacky.in_room(BARTS_CHAT)
        stacky.searches("gift")            # the memory_search tool's answer
        stacky.reads_page("marge")         # the memory_person tool's answer
        stacky.execs("memory find gift")   # a line the model runs with exec

    Every read starts a real `stack` process: the container-side shim itself,
    on `PATH`, talking to `host_api` below instead of the host's API.
    """

    def __init__(self, runtime, bin_dir: Path, port: int, monkeypatch):
        self.runtime, self.port = runtime, port
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("STACK_API_ADDR", f"127.0.0.1:{port}")
        monkeypatch.delenv("STACK_API_TOKEN", raising=False)
        monkeypatch.setenv("STACK_AUDIENCE_KEY", RUNTIME_KEY)
        # The address is the test's own port; in the container the shim's default.
        runtime["nanobot.agent.tools.shell"].ExecTool.allowed_env_keys = ["PATH", "STACK_API_ADDR"]
        self.in_room(None)

    def in_room(self, room: Room | None) -> "Stacky":
        """The next reads happen in `room`; None is a read outside any turn.

        Sets what nanobot's request context answers, on the stub nanobot a
        fixture builds for this one test: the message's metadata carries the
        room's people as the Matrix shim puts them there, none when nobody
        knows who is in the room."""
        context = sys.modules["nanobot.agent.tools.context"]
        request = None if room is None else types.SimpleNamespace(
            turn_id="t1", sender_id=f"@{room.asker}:simpson", session_key=f"matrix:{room.key}",
            chat_id=room.key,
            metadata={"room": room.label or room.key,
                      **({"room_people": list(room.people)} if room.people else {})})
        context.current_request_context = lambda: request
        self.request = request
        return self

    def briefs(self) -> list[str]:
        """The brief nanobot's runtime gives the model with the room's next message."""
        return sys.modules["brief"].brief_lines(self.request)

    def searches(self, query: str) -> str:
        """What the `memory_search` tool answers to `query`, as the model reads it."""
        return asyncio.run(sys.modules["memory_tool"].MemorySearchTool().execute(query=query))

    def reads_page(self, name: str) -> str:
        """What the `memory_person` tool answers for `name`."""
        return asyncio.run(sys.modules["person_tool"].MemoryPersonTool().execute(name=name))

    def execs(self, line: str, **env: str) -> str:
        """What exec prints for `stack <line>`, run in the environment nanobot's
        exec tool builds for the turn; `env` is what the line sets itself
        (`STACK_AUDIENCE=marge stack ...`)."""
        done = subprocess.run(["stack", *shlex.split(line)], capture_output=True, text=True,
                              timeout=60, env={**self.exec_env(), **env})
        return done.stdout + done.stderr

    def exit_code(self, line: str) -> int:
        """The exit status of `stack <line>` run with exec, as the model sees it."""
        return subprocess.run(["stack", *shlex.split(line)], capture_output=True, text=True,
                              timeout=60, env=self.exec_env()).returncode

    def exec_env(self) -> dict[str, str]:
        """The environment nanobot's exec tool builds for a command of the turn."""
        return self.runtime["nanobot.agent.tools.shell"].ExecTool()._build_env()

    def writes_line(self, line: str) -> str:
        """What the API answers to `line`, written to its port by a shell
        itself, past the shim, as any program holding the exec token can."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=60) as conn:
            conn.sendall(f"{line}\n".encode())
            conn.shutdown(socket.SHUT_WR)
            return b"".join(iter(lambda: conn.recv(4096), b"")).decode()

    def asks_history(self, scope: str) -> str:
        """What the `memory_history` tool answers for `scope`."""
        return asyncio.run(sys.modules["history_tool"].MemoryHistoryTool().execute(scope=scope))

    def edits_list(self, op: str, items: list[str], list_: str | None = None) -> str:
        """What the `list_edit` tool answers to `op` on `items` of `list_`."""
        return asyncio.run(sys.modules["list_tool"].ListEditTool().execute(
            op=op, list=list_, items=items))

    def deletes_list(self, list_: str, *, confirmed: bool | None = None,
                     turn: str = "t1") -> str:
        """What the `list_edit` tool answers to deleting `list_` in the
        message `turn` of the room, `confirmed` as the model passes it."""
        self.request.turn_id = turn
        return asyncio.run(sys.modules["list_tool"].ListEditTool().execute(
            op="delete", list=list_, confirmed=confirmed))

    def asks_calendar(self, **fields) -> str:
        """What the `calendar` tool answers to `fields`, as the model fills them."""
        return asyncio.run(sys.modules["calendar_tool"].CalendarTool().execute(**fields))


def host_api(memory, api, shim, allow):
    """The host API's side of the shim's wire, as a handler for `serving`.

    `allow` is what the API allows: its `DOMAIN_ALLOW` plus the admin's
    `[agent] allow`, read for every line, as `_domain_allow` reads it.

    It does what `handle_plaintext` does for the agent: the API's own
    `agent_command` turns the line into the command or a refusal, then the
    command runs and the answer is the text, with a status line on failure.
    It runs the command on the test's memory, not through the host's
    `./stack` (which would reach whatever runs on this Mac). One command at a
    time: the API runs each in a process of its own, while this one captures
    the test process's output, which two commands at once would share.
    """
    one_at_a_time = threading.Lock()

    def handle(conn) -> None:
        line = b""
        while not line.endswith(b"\n"):
            chunk = conn.recv(4096)
            if not chunk:
                break
            line += chunk
        args, refusal = api.agent_command(line.decode(), allow, RUNTIME_KEY)
        if refusal:
            conn.sendall(refusal.encode())
            return
        with one_at_a_time:
            code, text = run_command(memory, args)
        if code:
            text = f"{text.rstrip(chr(10))}\n{shim.EXIT_MARKER} {code}\n"
        conn.sendall(text.encode())
    return handle


def run_command(memory, args: list[str]) -> tuple[int, str]:
    """`./stack <args>` on the memory's instance, as `handle_plaintext` runs
    it: the CLI's real entry point in a process of its own, so the API's
    rules are tested against the reading of the line that runs (`main()`
    takes `--json` out, for one). Its exit code and what it printed."""
    done = subprocess.run(
        [sys.executable, "-m", "stack", *args], capture_output=True, text=True, timeout=60,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "lib"),
             "STACK_DIR": str(memory.stack.instance_dir)})
    return done.returncode, done.stdout or done.stderr


@contextlib.contextmanager
def stacky_serving(memory, api, shim, runtime, bin_dir: Path, monkeypatch) -> Iterator[Stacky]:
    """Stacky, with a real `stack` shim in `bin_dir` on `PATH` that talks to `host_api`;
    `stacky.allow` is the API's allowlist, which a test extends as the admin would."""
    bin_dir.mkdir()
    (bin_dir / "stack").symlink_to(REPO_ROOT / "stacklets" / "agent" / "client" / "stack")
    allow = list(api.DOMAIN_ALLOW)
    with serving(host_api(memory, api, shim, allow)) as port:
        stacky = Stacky(runtime, bin_dir, port, monkeypatch)
        # The admin's `[agent] allow`: a test adds an entry, as the admin would.
        stacky.allow = allow
        yield stacky
