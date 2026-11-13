"""The Server room sends a setup that needs the Mac to its terminal.

Someone writes `stack up <id>` in the Server room and the stacker bot runs
it through the host API. Some stacklets cannot be set up that way: their
manifest sets `attended_setup`, because their first run needs a person at
the Mac (macOS asks for permissions, the setup asks questions). The bot
reads that from the stacklets, as `stack list` reports them, and answers
with the command to run in the terminal instead of starting anything.

A start that another run of the stacklet refuses (the run lock, exit code
75) is reported as busy, with where that run comes from and since when,
not as a failure.

The chain is the real one up to Matrix: the stacker bot, the host API
(`stacklets/core/famstack-api.py`, served on loopback as a container
reaches it) and the real `./stack` as a process of its own, over a made-up
checkout with two stacklets: `vault`, whose setup is attended, and `app`.
Matrix is a recording client, and `docker` is a stand-in that answers
every command with success, as in `test_one_run_per_stacklet.py`.

A case starts at least one `./stack` process, about half a second each.
"""

from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import socket
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from stack import runs
from stacklets.core.bot.stacker import StackerBot

from tests.host_api_tokens import CORE_TOKEN, TOKEN_NAME, give_tokens

from .conftest import REPO, STACKLETS


# ── The checkout, the host API and the room ──────────────────────────────

@pytest.fixture
def checkout(tmp_path, monkeypatch) -> Path:
    """A checkout with `vault` (attended setup) and `app`, neither set up,
    and a `docker` on PATH that succeeds at everything."""
    root = tmp_path / "checkout"
    (root / ".stack").mkdir(parents=True)
    (root / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\ntimezone = "Europe/Berlin"\n')
    (root / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')
    manifests = {"vault": "attended_setup = true\n", "app": ""}
    for sid, extra in manifests.items():
        stacklet = root / "stacklets" / sid
        stacklet.mkdir(parents=True)
        (stacklet / "stacklet.toml").write_text(
            f'id = "{sid}"\nname = "{sid.title()}"\n{extra}')
        (stacklet / "docker-compose.yml").write_text(f"services:\n  {sid}:\n    image: busybox\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.delenv("STACK_DIR", raising=False)
    monkeypatch.delenv("STACK_RUN_SURFACE", raising=False)
    return root


def set_up(checkout: Path, sid: str) -> None:
    """`sid` has run its first setup, as its setup-done marker says."""
    (checkout / ".stack" / f"{sid}.setup-done").write_text("")


@pytest.fixture
def host_api(checkout, monkeypatch) -> None:
    """The host API on loopback, running the real `./stack` in `checkout`;
    the stacker's environment names its address."""
    path = STACKLETS / "core" / "famstack-api.py"
    loader = importlib.machinery.SourceFileLoader("famstack_api", str(path))
    spec = importlib.util.spec_from_file_location("famstack_api", path, loader=loader)
    api = importlib.util.module_from_spec(spec)
    loader.exec_module(api)
    monkeypatch.setattr(api, "STACK_BIN", REPO / "stack")
    monkeypatch.setattr(api, "REPO_ROOT", checkout)
    # Core's token, where the API reads it and where core's containers have it.
    give_tokens(checkout)
    monkeypatch.setenv(TOKEN_NAME, CORE_TOKEN)

    listener = socket.create_server(("127.0.0.1", 0))

    def serve():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=api.handle_client, args=(conn,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setenv("STACK_API_HOST", "127.0.0.1")
    monkeypatch.setenv("STACK_API_PORT", str(listener.getsockname()[1]))
    yield
    listener.close()


class ServerRoom:
    """The Server room: messages go to the stacker, its replies are kept."""

    def __init__(self, session_dir: Path):
        self.replies: list[str] = []
        session_dir.mkdir(parents=True, exist_ok=True)
        self.bot = StackerBot(homeserver="http://x", user_id="@stacker-bot:server",
                              password="x", session_dir=str(session_dir))
        self.bot._client = self

    async def write(self, body: str) -> list[str]:
        """Homer writes `body`; returns the bot's replies to it."""
        before = len(self.replies)
        event = SimpleNamespace(body=body, sender="@homer:server", event_id="$e:server",
                                server_timestamp=0, source={"content": {}})
        await self.bot._on_message(SimpleNamespace(room_id="!server:server"), event)
        return self.replies[before:]

    # What the bot calls on its Matrix client.
    async def room_send(self, room_id, message_type, content):
        self.replies.append(content["body"])

    async def update_receipt_marker(self, room_id, event_id):
        pass


@pytest.fixture
def room(tmp_path, host_api) -> ServerRoom:
    return ServerRoom(tmp_path / "session")


def started(checkout: Path, sid: str) -> bool:
    """A `stack up` of `sid` ran: it took the stacklet's run lock."""
    return runs.lock_path(checkout, sid).exists()


# ── A setup that needs the Mac ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_attended_setup_is_sent_to_the_terminal(room, checkout):
    """Given vault's manifest sets attended_setup and vault is not set up,
    when Homer writes `stack up vault`, then the one reply names the
    terminal command and nothing starts, because its setup needs a person
    at the Mac."""
    replies = await room.write("stack up vault")

    assert len(replies) == 1
    assert "`stack up vault`" in replies[0]
    assert not started(checkout, "vault")


@pytest.mark.asyncio
async def test_an_attended_stacklet_that_is_set_up_starts_from_the_chat(room, checkout):
    """Given vault is set up already, `stack up vault` starts it as any
    other stacklet: the setup that needed the Mac is done."""
    set_up(checkout, "vault")

    await room.write("stack up vault")

    assert started(checkout, "vault")


@pytest.mark.asyncio
async def test_a_stacklet_without_attended_setup_starts_from_the_chat(room, checkout):
    """Resting state: `stack up app` runs as it always did."""
    await room.write("stack up app")

    assert started(checkout, "app")


# ── A start that another run refuses ──────────────────────────────────────

@pytest.mark.asyncio
async def test_a_busy_stacklet_is_reported_as_starting_not_failed(room, checkout):
    """Given a run of vault goes on in the terminal, when Homer writes
    `stack up vault`, then the reply names where that run comes from and
    since when, and carries no failure."""
    set_up(checkout, "vault")
    with runs.hold(checkout, ["vault"]):
        holder = json.loads(runs.lock_path(checkout, "vault").read_text())
        replies = await room.write("stack up vault")

    since = datetime.fromisoformat(holder["started"]).strftime("%H:%M")
    assert "terminal" in replies[-1]
    assert since in replies[-1]
    assert "❌" not in replies[-1]


# ── Through the bot runner's handler ─────────────────────────────────────
# As the bot runner dispatches a message: through MicroBot's wrap, under its
# time limit on a handler, here 20 s instead of 180 s.

async def through_the_runner(room: ServerRoom, body: str) -> list[str]:
    """Homer writes `body`; the bot handles it as the bot runner does."""
    from nio import RoomMessageText
    room.bot.HANDLER_TIMEOUT_SECONDS = 20
    if not room.bot._handlers:
        room.bot.register_callbacks(None)
    handler = room.bot._handlers[0][1]
    event = RoomMessageText.from_dict({
        "type": "m.room.message", "sender": "@homer:server", "event_id": "$e:server",
        "origin_server_ts": 1, "content": {"msgtype": "m.text", "body": body}})
    before = len(room.replies)
    await handler(SimpleNamespace(room_id="!server:server"), event)
    return room.replies[before:]


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", ["up", "down", "restart"])
async def test_while_another_run_goes_on_the_room_is_told_at_once(room, checkout, cmd):
    """Given a run of vault goes on, when Homer writes `stack <cmd> app`,
    then the room is told at once which run goes on, and app's command
    changes nothing: runs go one at a time, and the room's command does not
    wait its turn, because its one answer must come within the API's limit.
    The run ahead lasts 15 s, longer than this test waits for any answer."""
    set_up(checkout, "app")
    holding, release = threading.Event(), threading.Event()

    def another_run():
        with runs.hold(checkout, ["vault"]):
            holding.set()
            release.wait(15)

    holder = threading.Thread(target=another_run)
    holder.start()
    holding.wait(10)
    try:
        replies = await asyncio.wait_for(through_the_runner(room, f"stack {cmd} app"), 10)
        still_going_on = not release.is_set() and holder.is_alive()
    finally:
        release.set()
        holder.join()

    assert still_going_on
    assert replies[-1].startswith("⏳"), replies
    assert "vault" in replies[-1]


@pytest.mark.asyncio
async def test_a_stop_that_fails_keeps_its_reason_in_the_run_log_and_the_room(
        room, checkout, tmp_path):
    """Given stopping app's containers fails, when Homer writes `stack down
    app`, then the room names the reason, and app's run log ends with the
    same error, so the dashboard's tile says why too."""
    set_up(checkout, "app")
    await through_the_runner(room, "stack up app")
    (tmp_path / "bin" / "docker").write_text(
        '#!/bin/sh\ncase "$*" in *stop*) echo "Error: port 42 is held by the moon" >&2; '
        'exit 1;; esac\nexit 0\n')

    replies = await through_the_runner(room, "stack down app")

    log = runs.lock_path(checkout, "app").with_suffix(".jsonl").read_text().splitlines()
    last = json.loads(log[-1])
    assert "moon" in replies[-1]
    assert last["type"] == "result" and "error" in last["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd, done", [("down", "⏸️"), ("restart", "🔄")])
async def test_a_stop_or_restart_that_succeeds_says_done(room, checkout, cmd, done):
    set_up(checkout, "app")
    await through_the_runner(room, "stack up app")

    replies = await through_the_runner(room, f"stack {cmd} app")

    assert replies[-1].startswith(done), replies


@pytest.mark.asyncio
async def test_a_refusal_for_another_run_leaves_the_stacklets_last_run_as_it_was(
        room, checkout):
    """Given app's last run ended and a run of vault goes on, when Homer
    writes `stack down app`, then the room is told it is busy and app's run
    log is unchanged: a refused command is no run of app, so a screen still
    shows app's last run and its result."""
    set_up(checkout, "app")
    await through_the_runner(room, "stack up app")
    log = runs.lock_path(checkout, "app").with_suffix(".jsonl")
    before = log.read_text()

    with runs.hold(checkout, ["vault"]):
        replies = await through_the_runner(room, "stack down app")

    assert replies[-1].startswith("⏳"), replies
    assert log.read_text() == before


@pytest.mark.asyncio
async def test_a_stop_that_succeeds_ends_as_done_on_a_screen(room, checkout):
    """A screen reads a run's result through `result_for_screens`: a stop
    that succeeded says so there too, not only in the room."""
    from stack.live import follow
    from stack.live.jobs import _state_of
    set_up(checkout, "app")
    await through_the_runner(room, "stack up app")

    await through_the_runner(room, "stack down app")

    end = list(follow.follow(checkout, "app"))[-1]
    assert _state_of(end.data) == "done", end.data


def test_a_restart_that_failed_is_not_followed_as_done(checkout, tmp_path):
    """Given stopping vault fails, when `stack restart app,vault` runs, then
    it exits with an error and app's run log ends with that error too,
    though app's own stop succeeded: the restart of app did not happen."""
    import subprocess
    import sys
    from stack.live import follow
    env = {**os.environ, "PYTHONPATH": str(REPO / "lib"), "HOME": str(checkout)}

    def stack(*args):
        return subprocess.run([sys.executable, "-m", "stack", *args], cwd=checkout, env=env,
                              text=True, capture_output=True, timeout=120,
                              stdin=subprocess.DEVNULL)

    set_up(checkout, "app")
    set_up(checkout, "vault")
    assert stack("up", "app,vault").returncode == 0
    (tmp_path / "bin" / "docker").write_text(
        '#!/bin/sh\ncase "$*" in *vault*stop*) echo "vault will not stop" >&2; exit 1;; '
        'esac\nexit 0\n')

    done = stack("restart", "app,vault")

    end = list(follow.follow(checkout, "app"))[-1]
    assert done.returncode != 0
    assert not end.data.get("ok") and end.data.get("error"), end.data
