"""MicroBot framework — typing indicator, handler timeout, error response.

The framework wraps every user-registered event callback so the family
gets a consistent "bot is working" signal and a useful error message
when a handler fails or runs too long. These tests pin that contract
without booting a real Matrix client.

The strategy: replace ``bot._client`` with a recording stub, call
``add_event_callback`` to register a handler under the wrap, then
invoke the wrapper directly with a synthetic event. Assertions go
against the typing/send calls the stub captured.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))

from microbot import MicroBot  # noqa: E402


# ── Stubs ────────────────────────────────────────────────────────────────

class FakeClient:
    """Drop-in for nio.AsyncClient covering exactly the surface the
    framework wrap touches: registering callbacks, toggling typing,
    sending messages."""

    def __init__(self):
        self.callbacks: list[tuple] = []
        self.typing_calls: list[tuple[str, bool]] = []
        self.sends: list[tuple[str, str, dict]] = []
        # Allow tests to inject a failure for typing / send.
        self.typing_raises: BaseException | None = None
        self.send_raises: BaseException | None = None
        # Canned parents for room_get_event, keyed by event_id. A missing
        # key yields a response whose `.event` is None (parent not found).
        self.parent_events: dict[str, object] = {}
        self.get_event_raises: BaseException | None = None
        # Drain surface: the timeline (newest-first), the rooms map, and a
        # sync token. room_messages returns the whole timeline in one page.
        self.next_batch = "END"
        self.rooms: dict = {}
        self.timeline: list = []
        # (room_id, event_id) read receipts the drain set for the "Seen by".
        self.receipts: list = []

    def add_event_callback(self, cb, event_type):
        self.callbacks.append((cb, event_type))

    async def room_messages(self, room_id, start, direction, limit):
        from nio import RoomMessagesResponse
        return RoomMessagesResponse(
            room_id=room_id, chunk=list(self.timeline), start=start, end=None,
        )

    async def update_receipt_marker(self, room_id, event_id, receipt_type=None, thread_id="main"):
        self.receipts.append((room_id, event_id))
        return SimpleNamespace()

    async def room_typing(self, room_id, typing_state, timeout):
        if self.typing_raises is not None:
            raise self.typing_raises
        self.typing_calls.append((room_id, typing_state))

    async def room_send(self, room_id, message_type, content):
        if self.send_raises is not None:
            raise self.send_raises
        self.sends.append((room_id, message_type, content))

    async def room_get_event(self, room_id, event_id):
        if self.get_event_raises is not None:
            raise self.get_event_raises
        return SimpleNamespace(event=self.parent_events.get(event_id))


def _build_bot(tmp_path, *, handler) -> tuple[MicroBot, FakeClient]:
    """Construct a minimal MicroBot subclass with a recording client.

    Returns the bot and its FakeClient. The given ``handler`` is
    registered via ``add_event_callback`` so the framework wrap is in
    play, and the resulting wrapper is also returned (as
    ``bot._wrapper``) for direct invocation.
    """
    class _Bot(MicroBot):
        name = "test-bot"
        # Tighter timeout so the timeout test doesn't slow the suite.
        HANDLER_TIMEOUT_SECONDS = 1

        def register_callbacks(self, client):
            return None

    bot = _Bot(
        homeserver="http://x",
        user_id="@test-bot:server",
        password="x",
        session_dir=str(tmp_path),
    )
    bot._client = FakeClient()
    # `object` matches any synthetic event in the drain's isinstance check;
    # the wrapper-contract tests invoke bot._wrapper directly and ignore it.
    bot.add_event_callback(handler, object)
    bot._wrapper = bot._handlers[0][1]
    return bot, bot._client


def _event(sender="@homer:server", ts=1_000_000, eid="$evt:server"):
    return SimpleNamespace(
        sender=sender,
        server_timestamp=ts,
        event_id=eid,
        source={"content": {}},
        body="hi",
    )


def _room(room_id="!r:server"):
    return SimpleNamespace(room_id=room_id)


# ── Happy path ───────────────────────────────────────────────────────────

class TestHappyPath:
    """Normal event — handler runs, framework clears typing in the
    finally, no error message is posted.

    The framework deliberately does NOT auto-set typing-on at the
    start of the wrap: handlers that post an intermediate confirmation
    message ("Received document from Homer…") need typing-on to land
    *after* that send, since Matrix clients clear the indicator on
    seeing a new message from the bot. So typing-on is the handler's
    job; typing-off is the framework's guarantee.
    """

    @pytest.mark.asyncio
    async def test_handler_runs_and_typing_cleared(self, tmp_path):
        handler_calls = []

        async def handler(room, event):
            handler_calls.append((room.room_id, event.sender))

        bot, client = _build_bot(tmp_path, handler=handler)
        await bot._wrapper(_room(), _event())

        # Framework cleared typing on exit even though the handler
        # never turned it on — finally is the safety net.
        assert client.typing_calls == [("!r:server", False)]
        assert handler_calls == [("!r:server", "@homer:server")]
        assert client.sends == []  # no error message in happy path

    @pytest.mark.asyncio
    async def test_handler_typing_then_framework_off(self, tmp_path):
        """A handler that turns typing on explicitly gets it cleared
        by the framework's finally — the canonical pattern."""

        async def handler(room, event):
            await bot._set_typing(room.room_id, on=True)

        bot, client = _build_bot(tmp_path, handler=handler)
        await bot._wrapper(_room(), _event())

        assert client.typing_calls == [
            ("!r:server", True),
            ("!r:server", False),
        ]


# ── Skip cases (no typing, no handler) ───────────────────────────────────

class TestSkipCases:
    """Events the framework drops before the wrap takes effect — no
    typing should flash and the handler must not run."""

    @pytest.mark.asyncio
    async def test_bot_sender_skipped(self, tmp_path):
        ran = []

        async def handler(room, event):
            ran.append(True)

        bot, client = _build_bot(tmp_path, handler=handler)
        await bot._wrapper(_room(), _event(sender="@test-bot:server"))

        assert ran == []
        assert client.typing_calls == []


# ── Drain: the timeline is the queue ────────────────────────────────────────


class TestDrain:
    """The drain is the delivery path: it reads the timeline past the
    per-room cursor and dispatches oldest-first, advancing the cursor
    after each handler returns (at-least-once). Dedup and ordering live
    here now, not in the wrapper."""

    @pytest.mark.asyncio
    async def test_dispatches_past_cursor_oldest_first_and_advances(self, tmp_path):
        seen = []

        async def handler(room, event):
            seen.append(event.server_timestamp)

        bot, client = _build_bot(tmp_path, handler=handler)
        room = _room()
        client.rooms = {room.room_id: room}
        bot._cursors[room.room_id] = 5
        # Timeline newest-first; only ts > 5 runs, and oldest-first.
        client.timeline = [
            _event(ts=7, eid="$e7"), _event(ts=6, eid="$e6"),
            _event(ts=5, eid="$e5"), _event(ts=4, eid="$e4"),
        ]

        await bot._drain()

        assert seen == [6, 7]
        assert bot._cursors[room.room_id] == 7
        # The public read receipt followed to the last processed event.
        assert client.receipts[-1] == (room.room_id, "$e7")

    @pytest.mark.asyncio
    async def test_own_messages_skipped_but_cursor_advances(self, tmp_path):
        seen = []

        async def handler(room, event):
            seen.append(event.server_timestamp)

        bot, client = _build_bot(tmp_path, handler=handler)
        room = _room()
        client.rooms = {room.room_id: room}
        bot._cursors[room.room_id] = 0
        client.timeline = [_event(ts=10, sender="@test-bot:server")]

        await bot._drain()

        assert seen == []                        # the bot's own message does nothing
        assert bot._cursors[room.room_id] == 10  # but the cursor moves past it

    @pytest.mark.asyncio
    async def test_first_sight_room_anchors_without_replaying_history(self, tmp_path):
        seen = []

        async def handler(room, event):
            seen.append(event.server_timestamp)

        bot, client = _build_bot(tmp_path, handler=handler)
        room = _room()
        client.rooms = {room.room_id: room}
        client.timeline = [_event(ts=100), _event(ts=99)]
        assert room.room_id not in bot._cursors

        await bot._drain()

        assert seen == []                       # history is not replayed
        assert bot._cursors[room.room_id] > 0   # cursor anchored at ~now


# ── Timeout ──────────────────────────────────────────────────────────────

class TestTimeout:
    """Handlers that run past HANDLER_TIMEOUT_SECONDS get cancelled and
    the framework posts a notice into the room."""

    @pytest.mark.asyncio
    async def test_timeout_posts_error_notice(self, tmp_path):
        async def handler(room, event):
            await asyncio.sleep(5)  # exceeds HANDLER_TIMEOUT_SECONDS=1

        bot, client = _build_bot(tmp_path, handler=handler)
        await bot._wrapper(_room(), _event())

        # Typing still cleared on the way out.
        assert ("!r:server", False) in client.typing_calls
        # An error notice was sent.
        assert len(client.sends) == 1
        room_id, mtype, content = client.sends[0]
        assert room_id == "!r:server"
        assert mtype == "m.room.message"
        assert content["msgtype"] == "m.notice"
        assert "wait" in content["body"].lower() or "longer" in content["body"].lower()
        # Reply-to threading is set so the user sees which message failed.
        assert content["m.relates_to"]["m.in_reply_to"]["event_id"] == "$evt:server"


# ── Exception ────────────────────────────────────────────────────────────

class TestException:
    """Unhandled exceptions in the handler must surface to the room as
    an error notice — silent failures are the worst case for a family
    bot."""

    @pytest.mark.asyncio
    async def test_exception_posts_error_notice(self, tmp_path):
        async def handler(room, event):
            raise RuntimeError("kaboom")

        bot, client = _build_bot(tmp_path, handler=handler)
        await bot._wrapper(_room(), _event())

        assert ("!r:server", False) in client.typing_calls
        assert len(client.sends) == 1
        _room_id, _mtype, content = client.sends[0]
        assert content["msgtype"] == "m.notice"
        # Default English message, no exception details leaked.
        assert "kaboom" not in content["body"]


# ── Format hook ──────────────────────────────────────────────────────────

class TestFormatHook:
    """Subclasses override `_format_handler_error` to localize or to
    map specific exception types to friendlier wording."""

    @pytest.mark.asyncio
    async def test_subclass_format_used(self, tmp_path):
        async def handler(room, event):
            raise ValueError("nope")

        bot, client = _build_bot(tmp_path, handler=handler)
        bot._format_handler_error = lambda ev, exc: "[stub] something is off"

        await bot._wrapper(_room(), _event())
        assert client.sends[0][2]["body"] == "[stub] something is off"

    @pytest.mark.asyncio
    async def test_timeout_routes_distinct_message(self, tmp_path):
        async def handler(room, event):
            await asyncio.sleep(5)

        bot, client = _build_bot(tmp_path, handler=handler)
        seen: list[type] = []

        def fmt(event, exc):
            seen.append(type(exc))
            if isinstance(exc, asyncio.TimeoutError):
                return "TIMEOUT"
            return "OTHER"

        bot._format_handler_error = fmt
        await bot._wrapper(_room(), _event())

        assert seen == [asyncio.TimeoutError]
        assert client.sends[0][2]["body"] == "TIMEOUT"


# ── Best-effort send ─────────────────────────────────────────────────────

class TestSendErrorRobust:
    """If the error-response send itself fails, the wrapper logs and
    moves on — it must not raise further or block the typing cleanup."""

    @pytest.mark.asyncio
    async def test_send_failure_does_not_propagate(self, tmp_path):
        async def handler(room, event):
            raise RuntimeError("kaboom")

        bot, client = _build_bot(tmp_path, handler=handler)
        client.send_raises = ConnectionError("synapse down")

        # Should complete cleanly even with both handler + send raising.
        await bot._wrapper(_room(), _event())
        # Typing still cleared.
        assert ("!r:server", False) in client.typing_calls


# ── Formatted send ────────────────────────────────────────────────────────


def _bare_bot(tmp_path) -> tuple[MicroBot, FakeClient]:
    """A MicroBot with a recording client and no handler wrap.

    Enough surface to exercise the transport helpers (`_send`,
    `_download_media`) directly.
    """
    class _Bot(MicroBot):
        name = "test-bot"

        def register_callbacks(self, client):
            return None

    bot = _Bot(
        homeserver="http://x",
        user_id="@test-bot:server",
        password="x",
        session_dir=str(tmp_path),
    )
    bot._client = FakeClient()
    return bot, bot._client


class TestSend:
    """`_send` is the framework's one formatted-reply path: markdown body
    rendered to HTML, optional reply relation, optional custom metadata
    merged onto the same message. Every bot uses this instead of hand-
    rolling content dicts, and it routes through `_room_send` so the
    typing-refresh seam stays in one place."""

    @pytest.mark.asyncio
    async def test_renders_markdown_to_html(self, tmp_path):
        bot, client = _bare_bot(tmp_path)
        await bot._send("!r:server", "**bold** and `code`")

        assert len(client.sends) == 1
        room_id, mtype, content = client.sends[0]
        assert room_id == "!r:server"
        assert mtype == "m.room.message"
        assert content["msgtype"] == "m.text"
        assert content["body"] == "**bold** and `code`"  # raw text preserved
        assert content["format"] == "org.matrix.custom.html"
        assert "<strong>bold</strong>" in content["formatted_body"]
        assert "<code>code</code>" in content["formatted_body"]

    @pytest.mark.asyncio
    async def test_no_reply_relation_by_default(self, tmp_path):
        bot, client = _bare_bot(tmp_path)
        await bot._send("!r:server", "hi")
        assert "m.relates_to" not in client.sends[0][2]

    @pytest.mark.asyncio
    async def test_reply_to_sets_in_reply_to(self, tmp_path):
        bot, client = _bare_bot(tmp_path)
        await bot._send("!r:server", "hi", reply_to="$evt:server")
        content = client.sends[0][2]
        assert content["m.relates_to"]["m.in_reply_to"]["event_id"] == "$evt:server"

    @pytest.mark.asyncio
    async def test_metadata_merged_onto_message(self, tmp_path):
        # The archivist rides a `dev.famstack.event` envelope on the same
        # visible message so a filing is one replayable timeline event.
        bot, client = _bare_bot(tmp_path)
        envelope = {
            "dev.famstack.event": {
                "type": "document.filed",
                "data": {"paperless_id": 42},
            },
        }
        await bot._send("!r:server", "Filed", reply_to="$e", metadata=envelope)

        content = client.sends[0][2]
        assert content["body"] == "Filed"
        assert content["dev.famstack.event"]["data"]["paperless_id"] == 42

    @pytest.mark.asyncio
    async def test_tables_extension_enabled(self, tmp_path):
        # Search results render as markdown tables — the extension must
        # survive the move into the framework.
        bot, client = _bare_bot(tmp_path)
        await bot._send("!r:server", "| a | b |\n|---|---|\n| 1 | 2 |")
        assert "<table>" in client.sends[0][2]["formatted_body"]


# ── Emoji reactions ─────────────────────────────────────────────────────


class TestReact:
    """`_react` annotates a message with an emoji (MSC2677) — the bot's
    way to signal state on a specific event without adding a timeline
    reply (e.g. 👀 the moment it picks up a capture). It routes through
    `_room_send` like every other send, and is best-effort: a reaction
    that fails must not crash the handler mid-capture."""

    @pytest.mark.asyncio
    async def test_sends_annotation_relation(self, tmp_path):
        bot, client = _bare_bot(tmp_path)
        await bot._react("!r:server", "$evt:server", "👀")

        assert len(client.sends) == 1
        room_id, mtype, content = client.sends[0]
        assert room_id == "!r:server"
        assert mtype == "m.reaction"
        rel = content["m.relates_to"]
        assert rel["rel_type"] == "m.annotation"
        assert rel["event_id"] == "$evt:server"
        assert rel["key"] == "👀"

    @pytest.mark.asyncio
    async def test_eyes_is_the_processing_signal(self, tmp_path):
        # The framework's "I'm working on this" convention, used to
        # replace the old "Received X, analyzing..." status messages.
        from microbot import EYES
        bot, client = _bare_bot(tmp_path)
        await bot._react("!r", "$e", EYES)
        assert client.sends[0][2]["m.relates_to"]["key"] == "\U0001F440"

    @pytest.mark.asyncio
    async def test_no_event_id_is_noop(self, tmp_path):
        bot, client = _bare_bot(tmp_path)
        await bot._react("!r:server", "", "👀")
        assert client.sends == []

    @pytest.mark.asyncio
    async def test_best_effort_swallows_send_failure(self, tmp_path):
        # A failed liveness reaction (homeserver hiccup, room not joined)
        # can't be allowed to kill the capture handler mid-flow.
        bot, client = _bare_bot(tmp_path)
        client.send_raises = RuntimeError("homeserver down")
        await bot._react("!r:server", "$evt:server", "👀")


# ── Reply-parent envelope ──────────────────────────────────────────────────


class TestReplyParentEnvelope:
    """`_reply_parent_envelope` is the transport half of reply-to-reprocess:
    given an event that replies to a prior message, fetch that parent,
    confirm the bot sent it, and hand back the `dev.famstack.event`
    envelope riding on it. Deciding what the envelope *means* (filing vs
    reclassify, which id) is the caller's job — this just retrieves it.

    Real-Synapse round-trip is covered by
    tests/integration/test_microbot_transport_e2e.py; these pin the
    branch logic offline."""

    @staticmethod
    def _reply_to(parent_id: str):
        return SimpleNamespace(source={"content": {
            "m.relates_to": {"m.in_reply_to": {"event_id": parent_id}},
        }})

    @pytest.mark.asyncio
    async def test_returns_envelope_from_our_parent(self, tmp_path):
        bot, client = _bare_bot(tmp_path)
        client.parent_events["$p"] = SimpleNamespace(
            sender="@test-bot:server",
            source={"content": {"dev.famstack.event": {
                "type": "document.filed", "data": {"paperless_id": 9},
            }}},
        )
        got = await bot._reply_parent_envelope("!r:server", self._reply_to("$p"))
        assert got == {"type": "document.filed", "data": {"paperless_id": 9}}

    @pytest.mark.asyncio
    async def test_none_when_not_a_reply(self, tmp_path):
        bot, _ = _bare_bot(tmp_path)
        plain = SimpleNamespace(source={"content": {"body": "just chatting"}})
        assert await bot._reply_parent_envelope("!r:server", plain) is None

    @pytest.mark.asyncio
    async def test_none_when_parent_not_ours(self, tmp_path):
        # A reply to ANOTHER user's message that happens to carry an
        # envelope must not fire — only the bot's own filings reprocess.
        bot, client = _bare_bot(tmp_path)
        client.parent_events["$p"] = SimpleNamespace(
            sender="@homer:server",
            source={"content": {"dev.famstack.event": {"type": "document.filed"}}},
        )
        assert await bot._reply_parent_envelope("!r:server", self._reply_to("$p")) is None

    @pytest.mark.asyncio
    async def test_none_when_parent_has_no_envelope(self, tmp_path):
        bot, client = _bare_bot(tmp_path)
        client.parent_events["$p"] = SimpleNamespace(
            sender="@test-bot:server",
            source={"content": {"body": "a plain bot message"}},
        )
        assert await bot._reply_parent_envelope("!r:server", self._reply_to("$p")) is None

    @pytest.mark.asyncio
    async def test_none_when_parent_missing(self, tmp_path):
        bot, _ = _bare_bot(tmp_path)
        # $p was never registered → response.event is None.
        assert await bot._reply_parent_envelope("!r:server", self._reply_to("$p")) is None

    @pytest.mark.asyncio
    async def test_none_when_fetch_raises(self, tmp_path):
        bot, client = _bare_bot(tmp_path)
        client.get_event_raises = ConnectionError("synapse down")
        assert await bot._reply_parent_envelope("!r:server", self._reply_to("$x")) is None
