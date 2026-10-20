"""StackerBot: commands in the Server Room, and how it answers.

Stacker used to hand-roll markdown→HTML with regex in its own
`_send_reply`, bypassing the framework. After the transport
consolidation it routes through `MicroBot._send` — real markdown
(bold → <strong>, not the regex era's <b>), reply relation, single
`_room_send` seam. These pin that by driving a command against a
recording client and inspecting the sent content.

The core API that runs the commands on the host is replaced at its
socket by a small TCP server that records what Stacker asked for.
"""

from __future__ import annotations

import json
import socketserver
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot"))

import stacker  # noqa: E402
from stacker import StackerBot  # noqa: E402


class _FakeClient:
    def __init__(self):
        self.sends: list[tuple[str, str, dict]] = []

    def add_event_callback(self, cb, event_type):
        pass

    async def room_send(self, room_id, message_type, content):
        self.sends.append((room_id, message_type, content))


def _build(tmp_path) -> tuple[StackerBot, _FakeClient]:
    bot = StackerBot(
        homeserver="http://x", user_id="@stacker-bot:server",
        password="x", session_dir=str(tmp_path),
    )
    bot._client = _FakeClient()
    return bot, bot._client


def _text(body, *, sender="@homer:server", event_id="$e:server"):
    return SimpleNamespace(
        body=body, sender=sender, event_id=event_id, source={"content": {}},
    )


def _room(room_id="!r:server"):
    return SimpleNamespace(room_id=room_id)


class TestStackerReply:

    @pytest.mark.asyncio
    async def test_help_renders_real_markdown_and_threads(self, tmp_path):
        bot, client = _build(tmp_path)
        await bot._on_message(_room(), _text("stack help"))

        assert len(client.sends) == 1
        room_id, mtype, content = client.sends[0]
        assert room_id == "!r:server"
        assert mtype == "m.room.message"
        assert content["format"] == "org.matrix.custom.html"
        # Real markdown: bold → <strong>, inline code → <code>.
        assert "<strong>Available commands</strong>" in content["formatted_body"]
        assert "<code>stack status</code>" in content["formatted_body"]
        # The regex era emitted <b>; the framework path must not.
        assert "<b>" not in content["formatted_body"]
        # Threaded to the triggering message.
        assert content["m.relates_to"]["m.in_reply_to"]["event_id"] == "$e:server"

    @pytest.mark.asyncio
    async def test_non_command_is_ignored(self, tmp_path):
        bot, client = _build(tmp_path)
        await bot._on_message(_room(), _text("good morning everyone"))
        assert client.sends == []


class _Api:
    """The core API's JSON protocol: one object in, one object out."""

    def __init__(self):
        self.requests: list[dict] = []
        self.reply: dict = {"ok": True}

    def serve(self):
        api = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                api.requests.append(json.loads(self.rfile.readline()))
                self.wfile.write((json.dumps(api.reply) + "\n").encode())

        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


@pytest.fixture
def api(monkeypatch):
    fake = _Api()
    server = fake.serve()
    monkeypatch.setattr(stacker, "API_HOST", "127.0.0.1")
    monkeypatch.setattr(stacker, "API_PORT", server.server_address[1])
    monkeypatch.setattr(stacker, "ADMIN_USERS", {"@homer:server"})
    yield fake
    server.shutdown()
    server.server_close()


def _replies(client):
    return [content["body"] for _, _, content in client.sends]


class TestRoomCreate:
    """`stack room create <name>`: an unencrypted family room, from chat."""

    @pytest.mark.asyncio
    async def test_the_name_is_passed_on_as_written(self, tmp_path, api):
        """Everything after `room create` is the name, case and colon kept:
        `Topic: Garden` is what makes the archivist treat it as a topic room."""
        bot, _ = _build(tmp_path)
        api.reply = {"ok": True, "created": True, "name": "Topic: Garden",
                     "link": "https://matrix.to/#/!new:server"}

        await bot._on_message(_room(), _text("stack room create Topic: Garden"))

        assert api.requests == [{"cmd": "room-create", "name": "Topic: Garden"}]

    @pytest.mark.asyncio
    async def test_the_reply_links_the_new_room(self, tmp_path, api):
        bot, client = _build(tmp_path)
        api.reply = {"ok": True, "created": True, "name": "Groceries",
                     "link": "https://matrix.to/#/!new:server"}

        await bot._on_message(_room(), _text("stack room create Groceries"))

        (reply,) = _replies(client)
        assert "Created" in reply
        assert "[Groceries](https://matrix.to/#/!new:server)" in reply

    @pytest.mark.asyncio
    async def test_a_name_that_is_taken_is_answered_with_a_link_to_that_room(self, tmp_path, api):
        bot, client = _build(tmp_path)
        api.reply = {"ok": True, "created": False, "name": "Groceries",
                     "link": "https://matrix.to/#/!old:server"}

        await bot._on_message(_room(), _text("stack room create Groceries"))

        (reply,) = _replies(client)
        assert "already exists" in reply
        assert "[Groceries](https://matrix.to/#/!old:server)" in reply

    @pytest.mark.asyncio
    async def test_a_failure_is_reported(self, tmp_path, api):
        bot, client = _build(tmp_path)
        api.reply = {"error": "Could not log in as any admin user"}

        await bot._on_message(_room(), _text("stack room create Groceries"))

        (reply,) = _replies(client)
        assert "Could not log in as any admin user" in reply

    @pytest.mark.asyncio
    async def test_only_an_admin_creates_a_room(self, tmp_path, api):
        bot, client = _build(tmp_path)

        await bot._on_message(_room(), _text("stack room create Groceries", sender="@bart:server"))

        assert api.requests == []
        assert _replies(client) == ["Only admins can run that command. Try `status` or `list`."]

    @pytest.mark.asyncio
    async def test_without_a_name_stacker_says_how_to_write_it(self, tmp_path, api):
        bot, client = _build(tmp_path)

        await bot._on_message(_room(), _text("stack room create"))

        assert api.requests == []
        (reply,) = _replies(client)
        assert "stack room create <name>" in reply

    @pytest.mark.asyncio
    async def test_help_lists_the_command(self, tmp_path, api):
        bot, client = _build(tmp_path)

        await bot._on_message(_room(), _text("stack help"))

        assert "stack room create <name>" in _replies(client)[0]


class TestOtherCommands:
    """Adding `room` left the commands that were there alone."""

    @pytest.mark.asyncio
    async def test_status_still_reaches_the_api(self, tmp_path, api):
        bot, _ = _build(tmp_path)
        api.reply = {"name": "famstack", "version": "1", "stacklets": []}

        await bot._on_message(_room(), _text("stack status", sender="@bart:server"))

        assert api.requests == [{"cmd": "status"}]

    @pytest.mark.asyncio
    async def test_up_still_names_its_stacklet(self, tmp_path, api, monkeypatch):
        bot, _ = _build(tmp_path)

        async def ack(room_id, event):
            pass
        monkeypatch.setattr(bot, "ack_event", ack)

        await bot._on_message(_room(), _text("stack up photos"))

        assert api.requests == [{"cmd": "up", "stacklet": "photos"}]
