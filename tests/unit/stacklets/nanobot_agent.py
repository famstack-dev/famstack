"""The agent's tools as nanobot registers them, run in a process of their own.

    with running_agent(tmp_path) as agent:
        agent.said(MARGES_CHAT, "marge", "the gift is a bowling bag")
        answer = agent.in_room(BARTS_CHAT, "bart", ["bart"]).calls(
            "read_file", path="memory/history.jsonl")

Stacky runs nanobot's gateway in its container. Its tools (`read_file`,
`list_dir`, `grep`, `find_files`, `exec`, `write_file`, `message`, ...) are
nanobot's own classes, configured by the agent's `config.json` and patched by
the runtime's shims (`stacklets/agent/runtime/sitecustomize.py`, loaded at
interpreter start because the runtime folder is on `PYTHONPATH`). What one
chat may read of another is decided there, so a test of it drives that code
and not a stand-in: the real `nanobot-ai` at the image's pin (the `test`
extra), the shipped `config.json`, the shipped runtime.

It runs in its own process, as in the container, because the shims patch
nanobot's classes for the whole interpreter, and the test process must not
keep them. The process holds one `AgentLoop` built as the gateway builds it
(`AgentLoop.from_config` with an empty `ToolRegistry`), over an agent home
the test fabricates (`<tmp>/home/.nanobot`, the folder compose mounts at
`/home/nanobot/.nanobot`). The test talks to it with one JSON line per
request on stdin and reads one JSON line back.

Before the loop starts, the process runs the start steps of
`stacklets/agent/entrypoint.sh` that prepare the agent's home (`START_STEPS`),
as the container does before `nanobot gateway`.

Two stand-ins, both at a boundary: `stack` on `PATH` is a script that prints
the line it was given (the real one forwards it to the host API on the Mac),
and the model is never called: a test names the tool call the model would
make, and a message a person sends runs nanobot's turn up to the model.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = REPO_ROOT / "stacklets" / "agent"

# The key the runtime signs a room's people with (compose: STACK_AUDIENCE_KEY).
RUNTIME_KEY = "runtime-audience-key-for-tests"

# What the stand-in `stack` prints before the arguments it got.
STACK_RAN = "stack ran:"

# The start steps of entrypoint.sh that prepare the agent's home, in order,
# with the arguments the entrypoint gives them ({home} is $NB).
START_STEPS = (
    ("dream_aside.py", "{home}", str(AGENT_DIR / "workspace"), "Stacky"),
    ("chat_aside.py", "{home}", str(AGENT_DIR / "workspace")),
)

SERVER = "simpson"

__all__ = ["RUNTIME_KEY", "STACK_RAN", "SERVER", "Turn", "RunningAgent",
           "fabricated_home", "running_agent"]


# ── The test's side ─────────────────────────────────────────────────────

@dataclass
class Turn:
    """One person's message in one room: the tool calls of its turn."""

    agent: "RunningAgent"
    room: str
    person: str
    people: list[str]

    def calls(self, tool: str, **params) -> str:
        """What the tool answers to the model, as text (an error included)."""
        return self.agent.request(op="tool", room=self.room, person=self.person,
                                  people=self.people, name=tool, params=params)["result"]

    def execs(self, command: str) -> str:
        """What exec answers to `command`: its output, or nanobot's refusal."""
        return self.calls("exec", command=command)


class RunningAgent:
    """The agent process over a fabricated home; see the module header."""

    def __init__(self, home: Path, process: subprocess.Popen):
        self.home = home
        self.nanobot = home / ".nanobot"
        self.workspace = self.nanobot / "workspace"
        self._process = process

    def request(self, **request) -> dict:
        """Send one operation to the agent process and return its answer;
        a crash in the process fails the test with its traceback."""
        assert self._process.stdin and self._process.stdout
        self._process.stdin.write(json.dumps(request) + "\n")
        self._process.stdin.flush()
        line = self._process.stdout.readline()
        if not line:
            raise AssertionError("the agent process ended:\n" + self._stderr())
        answer = json.loads(line)
        if "crash" in answer:
            raise AssertionError(answer["crash"])
        return answer

    def in_room(self, room: str, person: str, people: list[str]) -> Turn:
        """A turn of `person` in `room`, whose members are `people`."""
        return Turn(self, room, person, people)

    def said(self, room: str, person: str, text: str) -> None:
        """A message in `room`, kept in that room's session as nanobot keeps a turn."""
        self.request(op="said", room=room, person=person, text=text)

    def received(self, room: str, person: str, text: str, media: list[str]) -> None:
        """A message with the files the Matrix channel downloaded for it, taken
        in by nanobot's loop as a turn is, up to the model: the turn's stages
        restore, compact, command, build and save run, its run stage does not."""
        self.request(op="received", room=room, person=person, text=text, media=media)

    def summarised(self, room: str, text: str) -> None:
        """nanobot's consolidation archives a summary of `room`'s older turns."""
        self.request(op="summarised", room=room, text=text)

    def tools(self) -> list[str]:
        """The names of the tools nanobot registered, as the model sees them."""
        return self.request(op="tools")["tools"]

    def _stderr(self) -> str:
        return self._process.stderr.read() if self._process.stderr else ""


def fabricated_home(root: Path) -> Path:
    """An agent home as the entrypoint seeds it: config, profile files, skills."""
    nanobot = root / "home" / ".nanobot"
    workspace = nanobot / "workspace"
    workspace.mkdir(parents=True)
    (nanobot / "config.json").write_text((AGENT_DIR / "config.json").read_text())
    seed = AGENT_DIR / "workspace"
    for name in ("SOUL.md", "USER.md", "AGENTS.md"):
        (workspace / name).write_text((seed / name).read_text().replace("__AGENT_NAME__", "Stacky"))
    for skill in (seed / "skills").iterdir():
        target = workspace / "skills" / skill.name
        target.mkdir(parents=True)
        for f in skill.iterdir():
            (target / f.name).write_text(f.read_text())
    return root / "home"


@contextlib.contextmanager
def running_agent(root: Path, home: Path | None = None) -> Iterator[RunningAgent]:
    """Start the agent's start steps and its tools over `home` (fabricated in
    `root` when not given); stop the process when the block ends."""
    home = home or fabricated_home(root)
    env = _environment(root, home)
    process = subprocess.Popen(
        [sys.executable, "-P", __file__, str(home / ".nanobot")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=str(root))
    agent = RunningAgent(home, process)
    try:
        ready = agent.request(op="ready")
        assert ready.get("ready"), ready
        yield agent
    finally:
        with contextlib.suppress(Exception):
            assert process.stdin
            process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        for stream in (process.stdout, process.stderr):
            if stream:
                stream.close()


def _environment(root: Path, home: Path) -> dict[str, str]:
    """The container's environment, with paths on this Mac.

    `PYTHONPATH` is the runtime folder and a folder with the files compose
    mounts into `/app/shared`, so `sitecustomize` loads as in the image.
    """
    shared = root / "shared"
    shared.mkdir(exist_ok=True)
    for name in ("name_trigger.py", "agent_audience.py", "agent_confirm.py"):
        link = shared / name
        if not link.exists():
            link.symlink_to(REPO_ROOT / "lib" / "stack" / name)
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    stack = bin_dir / "stack"
    # It prints its environment too, so a test sees what exec hands a command.
    stack.write_text("#!/bin/sh\n" f'echo "{STACK_RAN} $*"\nenv\n')
    stack.chmod(0o755)
    return {
        "HOME": str(home),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "PYTHONPATH": f"{AGENT_DIR / 'runtime'}:{shared}",
        "TMPDIR": str(root),
        "AGENT_NAME": "Stacky",
        "AGENT_OPENAI_URL": "http://127.0.0.1:9/v1",
        "AGENT_OPENAI_KEY": "none",
        "AGENT_MODEL": "model",
        "AGENT_WHISPER_URL": "http://127.0.0.1:9/v1",
        "AGENT_WHISPER_KEY": "none",
        "AGENT_WHISPER_MODEL": "whisper",
        "AGENT_MATRIX_HOMESERVER": "http://127.0.0.1:9",
        "AGENT_MATRIX_SERVER_NAME": SERVER,
        "AGENT_BOT_ID": "stacky-bot",
        "AGENT_MATRIX_PASSWORD": "chat-password",
        "STACK_API_TOKEN": "agent-token",
        "STACK_AUDIENCE_KEY": RUNTIME_KEY,
    }


# ── The agent process ───────────────────────────────────────────────────

def _start_steps(nanobot: Path) -> None:
    """Run the entrypoint's start steps; a step that fails stops the agent."""
    runtime = AGENT_DIR / "runtime"
    for script, *args in START_STEPS:
        path = runtime / script
        if not path.exists():
            continue
        subprocess.run([sys.executable, str(path), *(a.format(home=nanobot) for a in args)],
                       check=True, stdout=sys.stderr)


async def _serve(nanobot: Path) -> None:
    from nanobot.agent.loop import AgentLoop
    from nanobot.agent.tools.context import RequestContext, request_context
    from nanobot.agent.tools.registry import ToolRegistry
    from nanobot.config.loader import load_config

    loop = AgentLoop.from_config(load_config(nanobot / "config.json"),
                                 tool_registry=ToolRegistry())

    def key(room: str) -> str:
        return f"matrix:{room}"

    async def handle(request: dict) -> dict:
        op = request["op"]
        if op == "ready":
            return {"ready": True}
        if op == "tools":
            return {"tools": sorted(loop.tools.tool_names)}
        if op == "said":
            session = loop.sessions.get_or_create(key(request["room"]))
            session.add_message("user", request["text"],
                                sender_id=f"@{request['person']}:{SERVER}")
            loop.sessions.save(session)
            return {}
        if op == "received":
            await _take_in(loop, request)
            return {}
        if op == "history":
            session = loop.sessions.get_or_create(key(request["room"]))
            return {"history": json.dumps(session.get_history(), ensure_ascii=False)}
        if op == "summarised":
            loop.context.memory.append_history(request["text"], session_key=key(request["room"]))
            return {}
        if op == "tool":
            from room_people import METADATA_KEY
            context = RequestContext(
                channel="matrix", chat_id=request["room"], session_key=key(request["room"]),
                sender_id=f"@{request['person']}:{SERVER}",
                metadata={METADATA_KEY: request["people"]})
            with request_context(context):
                result = await loop.tools.execute(request["name"], request["params"])
            return {"result": str(result)}
        return {"crash": f"unknown op {op}"}

    for line in sys.stdin:
        try:
            answer = await handle(json.loads(line))
        except Exception as error:  # the test sees the error, not a hang
            import traceback
            answer = {"crash": "".join(traceback.format_exception(error))}
        sys.stdout.write(json.dumps(answer) + "\n")
        sys.stdout.flush()


async def _take_in(loop, request: dict) -> None:
    """One inbound message through the stages of nanobot's turn
    (`AgentLoop._process_message`) but the model's: the run stage would ask
    the model, and offline nanobot cannot count the prompt's tokens."""
    import time

    from nanobot.agent.loop import TurnContext, TurnKind
    from nanobot.bus.events import InboundMessage

    msg = InboundMessage(channel="matrix", sender_id=f"@{request['person']}:{SERVER}",
                         chat_id=request["room"], content=request["text"],
                         media=request["media"])
    ctx = TurnContext(msg=msg, session_key=msg.session_key, turn_id=f"{msg.session_key}:{time.time_ns()}",
                      runtime=None, kind=TurnKind.USER,
                      delivery=loop.turn_delivery_factory.create(msg, msg.session_key),
                      original_user_text=msg.content)
    for stage, step in (("restore", loop._restore_turn), ("compact", loop._compact_session),
                        ("command", loop._dispatch_command), ("build", loop._build_turn),
                        ("save", loop._persist_turn)):
        await loop._run_turn_stage(ctx, stage, step)


def _main() -> None:
    """The agent process: start steps, then serve the test's operations."""
    nanobot = Path(sys.argv[1])
    _start_steps(nanobot)
    # nanobot logs to stdout; the replies own it.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    sys.stdout = real_stdout
    asyncio.run(_serve(nanobot))


if __name__ == "__main__":
    _main()
