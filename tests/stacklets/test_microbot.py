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


# ── Answer placement (thread vs inline) ─────────────────────────────────


class TestAnswer:
    """`_answer` posts the bot's reply to a processed item. It threads
    under the source message by default so routine filings stay out of
    the main timeline; the inline-reply path is preserved for rooms that
    opt out (a per-room knob — see interaction-patterns.md)."""

    @pytest.mark.asyncio
    async def test_threads_under_source_by_default(self, tmp_path):
        bot, client = _bare_bot(tmp_path)
        await bot._answer("!r:server", "Filed: passport", "$src:server")

        rel = client.sends[0][2]["m.relates_to"]
        assert rel["rel_type"] == "m.thread"
        assert rel["event_id"] == "$src:server"
        # First message in the thread falls back to replying to the root.
        assert rel["m.in_reply_to"]["event_id"] == "$src:server"

    @pytest.mark.asyncio
    async def test_inline_reply_when_room_opts_out(self, tmp_path):
        bot, client = _bare_bot(tmp_path)
        bot.REPLY_IN_THREAD = False  # the future per-room override, off
        await bot._answer("!r:server", "Filed: passport", "$src:server")

        rel = client.sends[0][2]["m.relates_to"]
        assert "rel_type" not in rel  # plain reply, not a thread
        assert rel["m.in_reply_to"]["event_id"] == "$src:server"

    @pytest.mark.asyncio
    async def test_no_source_event_posts_plain(self, tmp_path):
        bot, client = _bare_bot(tmp_path)
        await bot._answer("!r:server", "hello", None)
        assert "m.relates_to" not in client.sends[0][2]

    @pytest.mark.asyncio
    async def test_metadata_rides_along_in_thread(self, tmp_path):
        # The filing envelope must survive whichever placement is chosen.
        bot, client = _bare_bot(tmp_path)
        env = {"dev.famstack.event": {"type": "document.filed"}}
        await bot._answer("!r:server", "Filed", "$src:server", metadata=env)

        content = client.sends[0][2]
        assert content["m.relates_to"]["rel_type"] == "m.thread"
        assert content["dev.famstack.event"]["type"] == "document.filed"

    @pytest.mark.asyncio
    async def test_joins_existing_thread_when_source_is_threaded(self, tmp_path):
        # Filing a message that already lives in a thread must land the
        # answer in that thread (Matrix forbids nested threads), not root
        # a new one at the in-thread event.
        bot, client = _bare_bot(tmp_path)
        client.parent_events["$src:server"] = SimpleNamespace(
            source={"content": {"m.relates_to": {
                "rel_type": "m.thread", "event_id": "$root:server",
            }}},
        )
        await bot._answer("!r:server", "Filed", "$src:server")

        rel = client.sends[0][2]["m.relates_to"]
        assert rel["rel_type"] == "m.thread"
        assert rel["event_id"] == "$root:server"           # joins the existing thread
        assert rel["m.in_reply_to"]["event_id"] == "$src:server"  # quotes the upload

    @pytest.mark.asyncio
    async def test_thread_root_fetch_failure_falls_back_to_source(self, tmp_path):
        bot, client = _bare_bot(tmp_path)
        client.get_event_raises = ConnectionError("synapse down")
        await bot._answer("!r:server", "Filed", "$src:server")

        rel = client.sends[0][2]["m.relates_to"]
        assert rel["event_id"] == "$src:server"  # treated as top-level root


class TestThreadHelpers:
    """`get_thread_root` / `check_in_thread` — pure framework parsers so
    any bot can tell whether a message lives in a thread (and which one)
    straight off the event, no homeserver round-trip."""

    def test_root_of_threaded_event(self):
        evt = SimpleNamespace(source={"content": {"m.relates_to": {
            "rel_type": "m.thread", "event_id": "$root:server",
        }}})
        assert MicroBot.get_thread_root(evt) == "$root:server"
        assert MicroBot.check_in_thread(evt) is True

    def test_plain_reply_is_not_a_thread(self):
        # An m.in_reply_to that is NOT a thread relation is top-level.
        evt = SimpleNamespace(source={"content": {"m.relates_to": {
            "m.in_reply_to": {"event_id": "$x:server"},
        }}})
        assert MicroBot.get_thread_root(evt) is None
        assert MicroBot.check_in_thread(evt) is False

    def test_top_level_message(self):
        evt = SimpleNamespace(source={"content": {"body": "hi"}})
        assert MicroBot.get_thread_root(evt) is None
        assert MicroBot.check_in_thread(evt) is False

    def test_none_event_is_safe(self):
        assert MicroBot.get_thread_root(None) is None
        assert MicroBot.check_in_thread(None) is False


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


# ── Per-room config + emoji + !config command ────────────────────────────


class _FakeResp:
    """Minimal aiohttp-response stand-in (async context manager)."""

    def __init__(self, status, data=None):
        self.status = status
        self._data = data

    async def json(self):
        return self._data

    async def text(self):
        return str(self._data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeHttp:
    """Test double for the bot's aiohttp session backing room account
    data: an in-memory key→json store. ``raise_on`` forces a transport
    error to exercise the best-effort read path. We stub the network
    boundary here, not nio — the real REST round-trip is covered e2e."""

    def __init__(self):
        self.store: dict[str, dict] = {}
        self.raise_on: set[str] = set()

    def get(self, url, headers=None):
        if "get" in self.raise_on:
            raise RuntimeError("homeserver hiccup")
        if url in self.store:
            return _FakeResp(200, self.store[url])
        return _FakeResp(404)

    def put(self, url, headers=None, json=None):
        if "put" in self.raise_on:
            raise RuntimeError("homeserver hiccup")
        self.store[url] = json
        return _FakeResp(200, json)


class TestRoomConfig:
    """`get_room_config` / `set_room_config` read+write the bot's room
    account data over the Matrix REST API. No local cache — a read hits
    the homeserver every time, and a 404 or transient error reads as "no
    config" rather than crashing routing."""

    @staticmethod
    def _bot(tmp_path):
        bot, _ = _bare_bot(tmp_path)
        bot._client.access_token = "tok"
        bot._http = _FakeHttp()
        return bot, bot._http

    @pytest.mark.asyncio
    async def test_get_returns_empty_when_unset(self, tmp_path):
        bot, _ = self._bot(tmp_path)
        assert await bot.get_room_config("!r:server") == {}

    @pytest.mark.asyncio
    async def test_set_then_get_roundtrips(self, tmp_path):
        bot, http = self._bot(tmp_path)
        assert await bot.set_room_config("!r:server", process="react") is True
        assert await bot.get_room_config("!r:server") == {"process": "react"}
        # Stored under the room-scoped account-data URL for this bot.
        assert any("/account_data/dev.famstack.room" in u for u in http.store)

    @pytest.mark.asyncio
    async def test_set_merges_into_existing(self, tmp_path):
        bot, _ = self._bot(tmp_path)
        await bot.set_room_config("!r:server", process="react")
        await bot.set_room_config("!r:server", other="x")
        assert await bot.get_room_config("!r:server") == {
            "process": "react", "other": "x",
        }

    @pytest.mark.asyncio
    async def test_get_swallows_read_error(self, tmp_path):
        bot, http = self._bot(tmp_path)
        http.raise_on.add("get")
        assert await bot.get_room_config("!r:server") == {}

    @pytest.mark.asyncio
    async def test_set_reports_failure(self, tmp_path):
        bot, http = self._bot(tmp_path)
        http.raise_on.add("put")
        assert await bot.set_room_config("!r:server", process="react") is False


class TestNormalizeEmoji:
    """Reaction keys often carry the U+FE0F variation selector; matching
    a binding without normalizing silently misses those reactions."""

    def test_strips_variation_selector(self):
        assert MicroBot.normalize_emoji("\U0001F44D\uFE0F") == "\U0001F44D"

    def test_plain_emoji_unchanged(self):
        assert MicroBot.normalize_emoji("\U0001F516") == "\U0001F516"

    def test_none_is_safe(self):
        assert MicroBot.normalize_emoji(None) == ""


class TestConfigCommand:
    """`!config process auto|react` writes the room mode. Handled (returns
    True) so the caller stops routing it; non-config text passes through
    (returns False)."""

    @staticmethod
    def _bot(tmp_path):
        bot, client = _bare_bot(tmp_path)
        bot._client.access_token = "tok"
        bot._http = _FakeHttp()
        return bot, client

    @staticmethod
    def _evt(body):
        return SimpleNamespace(body=body, event_id="$e", source={"content": {}})

    @pytest.mark.asyncio
    async def test_sets_react_mode(self, tmp_path):
        bot, _ = self._bot(tmp_path)
        room = SimpleNamespace(room_id="!r:server")
        handled = await bot._maybe_handle_config_command(
            room, self._evt("!config process react"),
        )
        assert handled is True
        assert await bot.get_room_config("!r:server") == {"process": "react"}

    @pytest.mark.asyncio
    async def test_sets_auto_mode(self, tmp_path):
        bot, _ = self._bot(tmp_path)
        room = SimpleNamespace(room_id="!r:server")
        await bot._maybe_handle_config_command(
            room, self._evt("!config process auto"),
        )
        assert (await bot.get_room_config("!r:server"))["process"] == "auto"

    @pytest.mark.asyncio
    async def test_non_config_message_passes_through(self, tmp_path):
        bot, _ = self._bot(tmp_path)
        room = SimpleNamespace(room_id="!r:server")
        handled = await bot._maybe_handle_config_command(
            room, self._evt("just chatting"),
        )
        assert handled is False

    @pytest.mark.asyncio
    async def test_unknown_subcommand_consumed_with_usage(self, tmp_path):
        bot, client = self._bot(tmp_path)
        room = SimpleNamespace(room_id="!r:server")
        handled = await bot._maybe_handle_config_command(
            room, self._evt("!config wat"),
        )
        assert handled is True              # consumed, not routed onward
        assert await bot.get_room_config("!r:server") == {}  # nothing written
        assert any("Usage" in c[2].get("body", "") for c in client.sends)

    @pytest.mark.asyncio
    async def test_write_failure_reported_not_acked(self, tmp_path):
        # A failed write must not be acked as success — the regression the
        # power-level eval exposed (state-event write silently rejected).
        bot, client = self._bot(tmp_path)
        bot._http.raise_on.add("put")
        room = SimpleNamespace(room_id="!r:server")
        handled = await bot._maybe_handle_config_command(
            room, self._evt("!config process react"),
        )
        assert handled is True
        body = client.sends[-1][2].get("body", "")
        assert "Couldn't save" in body and "react mode" not in body
