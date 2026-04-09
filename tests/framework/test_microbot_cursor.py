"""Test MicroBot message cursor — at-most-once delivery.

The cursor ensures bots don't reprocess messages after a restart.
We test the mechanism in isolation — no Matrix server needed.

Requires matrix-nio (real import, not mocked). Only loguru is
stubbed since it's just logging. Run with:
  uvx --with loguru --with matrix-nio pytest tests/framework/test_microbot_cursor.py
"""

import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Stub loguru only — it's just logging, not worth pulling in
if "loguru" not in sys.modules:
    _loguru = types.ModuleType("loguru")
    _loguru.logger = MagicMock()
    sys.modules["loguru"] = _loguru

try:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "stacklets" / "core" / "bot-runner"))
    from microbot import MicroBot
except ImportError:
    pytest.skip("matrix-nio not installed", allow_module_level=True)


class StubBot(MicroBot):
    name = "stub-bot"

    def __init__(self, session_dir, **kw):
        super().__init__("http://localhost", "@stub:test", "pass", str(session_dir), **kw)

    def register_callbacks(self, client):
        pass


def _make_event(sender, server_timestamp, event_id="$evt"):
    event = MagicMock()
    event.sender = sender
    event.server_timestamp = server_timestamp
    event.event_id = event_id
    return event


def _make_room(room_id="!room:test"):
    room = MagicMock()
    room.room_id = room_id
    return room


class TestCursorFiltering:
    def test_new_message_delivered(self, tmp_path):
        bot = StubBot(tmp_path)
        bot._client = MagicMock()

        delivered = []
        async def handler(room, event):
            delivered.append(event.server_timestamp)

        bot.add_event_callback(handler, MagicMock)

        # The wrapper was registered on the mock client
        wrapper = bot._client.add_event_callback.call_args[0][0]

        import asyncio
        room = _make_room()
        event = _make_event("@alice:test", 1000)
        asyncio.run(wrapper(room, event))

        assert delivered == [1000]

    def test_old_message_filtered(self, tmp_path):
        bot = StubBot(tmp_path)
        bot._client = MagicMock()

        delivered = []
        async def handler(room, event):
            delivered.append(event.server_timestamp)

        bot.add_event_callback(handler, MagicMock)
        wrapper = bot._client.add_event_callback.call_args[0][0]

        import asyncio
        room = _make_room()

        # First message advances cursor
        asyncio.run(wrapper(room, _make_event("@alice:test", 1000)))
        # Second message is older — should be filtered
        asyncio.run(wrapper(room, _make_event("@alice:test", 999)))
        # Same timestamp — also filtered (<=)
        asyncio.run(wrapper(room, _make_event("@alice:test", 1000)))

        assert delivered == [1000]

    def test_own_messages_filtered(self, tmp_path):
        bot = StubBot(tmp_path)
        bot._client = MagicMock()

        delivered = []
        async def handler(room, event):
            delivered.append(True)

        bot.add_event_callback(handler, MagicMock)
        wrapper = bot._client.add_event_callback.call_args[0][0]

        import asyncio
        room = _make_room()
        event = _make_event("@stub:test", 1000)  # same as bot's user_id
        asyncio.run(wrapper(room, event))

        assert delivered == []

    def test_per_room_cursors(self, tmp_path):
        bot = StubBot(tmp_path)
        bot._client = MagicMock()

        delivered = []
        async def handler(room, event):
            delivered.append((room.room_id, event.server_timestamp))

        bot.add_event_callback(handler, MagicMock)
        wrapper = bot._client.add_event_callback.call_args[0][0]

        import asyncio
        room_a = _make_room("!a:test")
        room_b = _make_room("!b:test")

        # Message in room A at t=1000
        asyncio.run(wrapper(room_a, _make_event("@alice:test", 1000)))
        # Message in room B at t=500 — different room, should be delivered
        asyncio.run(wrapper(room_b, _make_event("@alice:test", 500)))

        assert delivered == [("!a:test", 1000), ("!b:test", 500)]

    def test_cursor_survives_restart(self, tmp_path):
        """Simulate a restart: create bot, process message, create new bot, verify filtered."""
        import asyncio

        # First bot instance processes a message
        bot1 = StubBot(tmp_path)
        bot1._client = MagicMock()
        delivered1 = []
        async def h1(room, event):
            delivered1.append(event.server_timestamp)
        bot1.add_event_callback(h1, MagicMock)
        wrapper1 = bot1._client.add_event_callback.call_args[0][0]
        asyncio.run(wrapper1(_make_room(), _make_event("@alice:test", 2000)))
        assert delivered1 == [2000]

        # Second bot instance (restart) — same session dir
        bot2 = StubBot(tmp_path)
        bot2._client = MagicMock()
        delivered2 = []
        async def h2(room, event):
            delivered2.append(event.server_timestamp)
        bot2.add_event_callback(h2, MagicMock)
        wrapper2 = bot2._client.add_event_callback.call_args[0][0]

        # Same message replayed — should be filtered
        asyncio.run(wrapper2(_make_room(), _make_event("@alice:test", 2000)))
        # New message — should be delivered
        asyncio.run(wrapper2(_make_room(), _make_event("@alice:test", 3000)))

        assert delivered2 == [3000]

    def test_cursor_file_format(self, tmp_path):
        """Cursor file is simple JSON: {room_id: timestamp}."""
        import asyncio

        bot = StubBot(tmp_path)
        bot._client = MagicMock()
        bot.add_event_callback(AsyncMock(), MagicMock)
        wrapper = bot._client.add_event_callback.call_args[0][0]

        asyncio.run(wrapper(_make_room("!r:test"), _make_event("@a:test", 42000)))

        raw = json.loads((tmp_path / "stub-bot-cursor").read_text())
        assert raw == {"!r:test": 42000}

    def test_corrupt_cursor_file_recovers(self, tmp_path):
        """A corrupt cursor file is treated as empty — bot reprocesses."""
        (tmp_path / "stub-bot-cursor").write_text("garbage{{{")

        bot = StubBot(tmp_path)
        assert bot._cursors == {}
