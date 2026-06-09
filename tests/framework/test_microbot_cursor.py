"""Test MicroBot message cursor — at-most-once delivery.

The cursor ensures bots don't reprocess messages after a restart.
We test the mechanism in isolation — no Matrix server needed.

Architecture (kept in sync with `microbot.py`):

  - `add_event_callback` wraps a handler with timeout + own-message
    filter + error response, and stores the wrapper in `self._handlers`.
    It does NOT register with nio anymore.
  - `_drain_room` is what actually delivers messages: it pages the
    timeline backward from the live sync position, filters events
    older than the per-room cursor, then dispatches the rest oldest-
    first through `_dispatch`.
  - `_dispatch` iterates `self._handlers` and runs every wrapper whose
    `event_type` matches the event.
  - `_advance_cursor` writes `{room_id: server_timestamp}` to disk
    after each successful dispatch.

These tests drive the smaller pieces (wrapper, dispatch, cursor file)
plus one end-to-end drain test to pin the cursor-filtering behaviour
the original tests were written for.

Requires matrix-nio (real import, not mocked). Only loguru is stubbed
since it's just logging. Run with:
  uvx --with loguru --with matrix-nio pytest tests/framework/test_microbot_cursor.py
"""

import asyncio
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Stub loguru only — it's just logging, not worth pulling in.
if "loguru" not in sys.modules:
    _loguru = types.ModuleType("loguru")
    _loguru.logger = MagicMock()
    sys.modules["loguru"] = _loguru

try:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "stacklets" / "core" / "bot-runner"))
    from microbot import MicroBot
    from nio import RoomMessagesResponse, SyncResponse
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


def _mock_response(chunk, *, end=None):
    """A duck-typed `RoomMessagesResponse` that passes the isinstance
    check in `_drain_room`. The microbot pages the timeline backward
    until either `end` is None (no more history) or the cursor is
    crossed; a single-page response with `end=None` exits cleanly."""

    resp = MagicMock(spec=RoomMessagesResponse)
    resp.chunk = list(chunk)
    resp.start = "anchor"
    resp.end = end
    return resp


def _mock_client(*, chunks, rooms=None, next_batch="sync_token"):
    """Mock the nio surface `_drain_room` touches.

    ``chunks`` is a list of event lists, one per page of `room_messages`
    the drain consumes (newest-first within each page, since the drain
    walks backward). When the drain runs out of chunks it sees a
    response with `end=None` and stops paging.
    """

    client = MagicMock()
    client.next_batch = next_batch
    client.rooms = rooms or {"!room:test": _make_room()}
    client.room_read_markers = AsyncMock()
    page_iter = iter(chunks)

    async def _room_messages(*args, **kwargs):
        try:
            chunk = next(page_iter)
        except StopIteration:
            return _mock_response([], end=None)
        return _mock_response(chunk, end=None)

    client.room_messages = _room_messages
    return client


# ── Wrapper (own-message filter, dispatch routing) ───────────────────────


class TestWrapper:
    """The wrapper installed by `add_event_callback` does two things:
    it short-circuits when the event's sender matches the bot's user_id
    (so the bot never reprocesses its own messages), and it runs the
    inner callback under a timeout. We pin both behaviours here; the
    cursor-driven delivery lives in `_drain_room` (covered below)."""

    def test_own_messages_filtered_by_wrapper(self, tmp_path):
        bot = StubBot(tmp_path)
        delivered = []

        async def handler(room, event):
            delivered.append(event.event_id)

        bot.add_event_callback(handler, MagicMock)
        # The wrapper is stored as the second element of the handler
        # tuple. `_handlers` is the canonical place to look it up.
        assert len(bot._handlers) == 1
        wrapper = bot._handlers[0][1]

        own_event = _make_event("@stub:test", 1000, event_id="$own")
        asyncio.run(wrapper(_make_room(), own_event))
        assert delivered == []

    def test_other_users_delivered_by_wrapper(self, tmp_path):
        bot = StubBot(tmp_path)
        # Stub `_set_typing` so the wrapper's finally clause doesn't
        # try to call the (absent) Matrix client. The cursor tests run
        # offline; only the delivery semantics matter here.
        bot._set_typing = AsyncMock()
        delivered = []

        async def handler(room, event):
            delivered.append(event.event_id)

        bot.add_event_callback(handler, MagicMock)
        wrapper = bot._handlers[0][1]

        other_event = _make_event("@alice:test", 1000, event_id="$other")
        asyncio.run(wrapper(_make_room(), other_event))
        assert delivered == ["$other"]


# ── Dispatch (event-type routing) ─────────────────────────────────────────


class TestDispatch:
    """`_dispatch` iterates `_handlers` and invokes the wrapper for
    every event-type match. Handlers registered for a different type
    do not fire."""

    def test_matching_handler_dispatched(self, tmp_path):
        bot = StubBot(tmp_path)
        bot._set_typing = AsyncMock()
        bot._client = _mock_client(chunks=[])

        delivered = []

        async def handler(room, event):
            delivered.append(event.event_id)

        # MagicMock is the registered event type; the dispatched event
        # is a MagicMock so isinstance(event, MagicMock) is True.
        bot.add_event_callback(handler, MagicMock)

        event = _make_event("@alice:test", 1000, event_id="$x")
        asyncio.run(bot._dispatch("!room:test", event))
        assert delivered == ["$x"]

    def test_unrelated_handler_not_dispatched(self, tmp_path):
        bot = StubBot(tmp_path)
        bot._set_typing = AsyncMock()
        bot._client = _mock_client(chunks=[])

        delivered = []

        async def handler(room, event):
            delivered.append(event.event_id)

        # Register for `str` — the MagicMock event won't be a str
        # instance, so dispatch must skip this handler.
        bot.add_event_callback(handler, str)

        event = _make_event("@alice:test", 1000, event_id="$x")
        asyncio.run(bot._dispatch("!room:test", event))
        assert delivered == []


# ── Cursor file format + persistence ──────────────────────────────────────


class TestCursorFile:
    """The cursor file is a JSON dict mapping room id to the last-seen
    server_timestamp. `_advance_cursor` writes it; `_load_cursors`
    reads it on bot construction so cursors survive restarts."""

    def test_advance_writes_json_dict(self, tmp_path):
        bot = StubBot(tmp_path)
        bot._advance_cursor("!r:test", 42000)
        raw = json.loads((tmp_path / "stub-bot-cursor").read_text())
        assert raw == {"!r:test": 42000}

    def test_per_room_cursors(self, tmp_path):
        bot = StubBot(tmp_path)
        bot._advance_cursor("!a:test", 1000)
        bot._advance_cursor("!b:test", 500)
        raw = json.loads((tmp_path / "stub-bot-cursor").read_text())
        assert raw == {"!a:test": 1000, "!b:test": 500}

    def test_cursor_survives_restart(self, tmp_path):
        """A new bot instance reads the cursor file on construction.
        Replays do not advance the cursor backward, so the second-bot
        view of the room starts at the timestamp the first bot left."""
        bot1 = StubBot(tmp_path)
        bot1._advance_cursor("!r:test", 2000)

        bot2 = StubBot(tmp_path)
        assert bot2._cursors == {"!r:test": 2000}

    def test_corrupt_cursor_file_recovers(self, tmp_path):
        """A corrupt cursor file is treated as empty -- bot reprocesses."""
        (tmp_path / "stub-bot-cursor").write_text("garbage{{{")
        bot = StubBot(tmp_path)
        assert bot._cursors == {}


# ── _drain_room (end-to-end cursor filtering) ─────────────────────────────


class TestDrainRoomCursorFiltering:
    """`_drain_room` is where the cursor actually filters delivery: it
    walks the timeline backward, takes events newer than the cursor,
    and dispatches them oldest-first. After each dispatch the cursor
    advances to that event's timestamp.

    These tests pre-seed the per-room cursor and mock the timeline
    response so we can pin exactly which events get delivered and
    which get filtered."""

    def _drive(self, tmp_path, *, room_cursor: int,
               chunk: list, handler=None):
        bot = StubBot(tmp_path)
        bot._set_typing = AsyncMock()
        bot._set_read_receipt = AsyncMock()
        bot._cursors["!room:test"] = room_cursor

        delivered = []

        async def default_handler(room, event):
            delivered.append(event.server_timestamp)

        bot.add_event_callback(handler or default_handler, MagicMock)
        bot._client = _mock_client(chunks=[chunk])
        asyncio.run(bot._drain_room("!room:test"))
        return bot, delivered

    def test_new_message_delivered(self, tmp_path):
        """A message with `server_timestamp` above the cursor lands
        in the handler."""
        _, delivered = self._drive(
            tmp_path, room_cursor=500,
            chunk=[_make_event("@alice:test", 1000)],
        )
        assert delivered == [1000]

    def test_old_message_filtered(self, tmp_path):
        """A message at or below the cursor is filtered before dispatch.
        The drain uses `<=` so a same-timestamp event is also dropped."""
        _, delivered = self._drive(
            tmp_path, room_cursor=1000,
            chunk=[
                _make_event("@alice:test", 1000, event_id="$same"),
                _make_event("@alice:test", 999, event_id="$older"),
            ],
        )
        assert delivered == []

    def test_first_sight_of_room_anchors_cursor(self, tmp_path):
        """A room not yet in the cursor map gets anchored to "now" and
        nothing is delivered for that pass. Without this, joining a
        new room would replay its entire history."""
        bot = StubBot(tmp_path)
        bot._set_typing = AsyncMock()
        delivered = []

        async def handler(room, event):
            delivered.append(event.server_timestamp)

        bot.add_event_callback(handler, MagicMock)
        bot._client = _mock_client(
            chunks=[[_make_event("@alice:test", 1000)]],
        )

        # No pre-seeded cursor for !room:test.
        asyncio.run(bot._drain_room("!room:test"))
        assert delivered == []
        assert "!room:test" in bot._cursors

    def test_cursor_advances_after_delivery(self, tmp_path):
        """The cursor moves to the timestamp of each event after its
        handler returns. A subsequent drain over the same chunk now
        treats the event as past."""
        bot, delivered = self._drive(
            tmp_path, room_cursor=500,
            chunk=[
                _make_event("@alice:test", 700),
                _make_event("@alice:test", 800),
            ],
        )
        assert delivered == [700, 800]
        assert bot._cursors["!room:test"] == 800

    def test_per_room_drain(self, tmp_path):
        """Independent cursors per room: a drain for room A doesn't
        change room B's cursor."""
        bot = StubBot(tmp_path)
        bot._set_typing = AsyncMock()
        bot._set_read_receipt = AsyncMock()
        bot._cursors["!a:test"] = 500
        bot._cursors["!b:test"] = 500

        delivered = []

        async def handler(room, event):
            delivered.append((room.room_id, event.server_timestamp))

        bot.add_event_callback(handler, MagicMock)
        rooms = {
            "!a:test": _make_room("!a:test"),
            "!b:test": _make_room("!b:test"),
        }
        bot._client = _mock_client(
            chunks=[[_make_event("@alice:test", 1000)]],
            rooms=rooms,
        )
        asyncio.run(bot._drain_room("!a:test"))
        assert delivered == [("!a:test", 1000)]
        assert bot._cursors == {"!a:test": 1000, "!b:test": 500}


# ── on_room_joined deferral via SyncResponse ──────────────────────────────


class TestOnRoomJoinedDeferral:
    """`on_room_joined` is the framework's contract that subclasses see a
    room with populated state. Invite-accept queues the room id; the
    sync-response callback `_on_sync_for_pending_joins` fires the hook
    exactly once when the next sync has populated the room.

    These tests exercise that callback directly without standing up a
    real sync loop, so the framework contract is pinned independently
    of nio's internals."""

    def _bot_with_hook(self, tmp_path):
        bot = StubBot(tmp_path)
        bot._client = MagicMock()
        bot._client.rooms = {}
        calls: list[str] = []

        async def on_joined(room_id):
            calls.append(room_id)

        bot.on_room_joined = on_joined
        return bot, calls

    def test_fires_once_state_arrives(self, tmp_path):
        bot, calls = self._bot_with_hook(tmp_path)
        bot._pending_room_joins.add("!room:test")
        # Sync arrives but room state not yet populated; hook stays
        # queued, no call.
        asyncio.run(bot._on_sync_for_pending_joins(
            MagicMock(spec=SyncResponse),
        ))
        assert calls == []
        assert bot._pending_room_joins == {"!room:test"}

        # Next sync: room appears with members. Hook fires; queue drains.
        bot._client.rooms["!room:test"] = MagicMock(users={"@a:test": object()})
        asyncio.run(bot._on_sync_for_pending_joins(
            MagicMock(spec=SyncResponse),
        ))
        assert calls == ["!room:test"]
        assert bot._pending_room_joins == set()

    def test_room_present_but_no_members_still_waits(self, tmp_path):
        """A room that appears with an empty users dict is not yet
        usable -- the hook must wait for membership to populate too."""
        bot, calls = self._bot_with_hook(tmp_path)
        bot._pending_room_joins.add("!room:test")
        bot._client.rooms["!room:test"] = MagicMock(users={})
        asyncio.run(bot._on_sync_for_pending_joins(
            MagicMock(spec=SyncResponse),
        ))
        assert calls == []
        assert bot._pending_room_joins == {"!room:test"}

    def test_does_not_fire_twice_for_same_room(self, tmp_path):
        """Once the hook fires, the room id leaves the queue. A
        subsequent sync (covering unrelated activity) doesn't trigger
        a second hook call."""
        bot, calls = self._bot_with_hook(tmp_path)
        bot._pending_room_joins.add("!room:test")
        bot._client.rooms["!room:test"] = MagicMock(users={"@a:test": object()})
        asyncio.run(bot._on_sync_for_pending_joins(
            MagicMock(spec=SyncResponse),
        ))
        assert calls == ["!room:test"]
        # Second sync, room still present, queue empty -- no hook call.
        asyncio.run(bot._on_sync_for_pending_joins(
            MagicMock(spec=SyncResponse),
        ))
        assert calls == ["!room:test"]

    def test_ignores_non_sync_responses(self, tmp_path):
        """nio's response callbacks receive every response; the room-
        join handler must only act on SyncResponse."""
        bot, calls = self._bot_with_hook(tmp_path)
        bot._pending_room_joins.add("!room:test")
        bot._client.rooms["!room:test"] = MagicMock(users={"@a:test": object()})
        # Pass something that isn't a SyncResponse -- should be ignored.
        asyncio.run(bot._on_sync_for_pending_joins(MagicMock()))
        assert calls == []
        assert bot._pending_room_joins == {"!room:test"}

    def test_hook_exception_does_not_crash_or_requeue(self, tmp_path):
        """A subclass `on_room_joined` that raises must not break the
        callback or leave the room queued. Errors are logged and the
        room is treated as handled."""
        bot = StubBot(tmp_path)
        bot._client = MagicMock()
        bot._client.rooms = {"!room:test": MagicMock(users={"@a:test": object()})}
        bot._pending_room_joins.add("!room:test")

        async def boom(_rid):
            raise RuntimeError("hook bug")

        bot.on_room_joined = boom
        # Should not raise.
        asyncio.run(bot._on_sync_for_pending_joins(
            MagicMock(spec=SyncResponse),
        ))
        assert bot._pending_room_joins == set()
