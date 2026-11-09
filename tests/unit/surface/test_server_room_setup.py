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
