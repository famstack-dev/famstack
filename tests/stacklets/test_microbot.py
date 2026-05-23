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

    def add_event_callback(self, cb, event_type):
        self.callbacks.append((cb, event_type))

    async def room_typing(self, room_id, typing_state, timeout):
        if self.typing_raises is not None:
            raise self.typing_raises
        self.typing_calls.append((room_id, typing_state))

    async def room_send(self, room_id, message_type, content):
        if self.send_raises is not None:
            raise self.send_raises
        self.sends.append((room_id, message_type, content))


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
    bot.add_event_callback(handler, "RoomMessageText")
    bot._wrapper = bot._client.callbacks[0][0]
    return bot, bot._client


def _event(sender="@homer:server", ts=1_000_000):
    return SimpleNamespace(
        sender=sender,
        server_timestamp=ts,
        event_id="$evt:server",
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

    @pytest.mark.asyncio
    async def test_replay_event_skipped(self, tmp_path):
        ran = []

        async def handler(room, event):
            ran.append(True)

        bot, client = _build_bot(tmp_path, handler=handler)
        # First event advances the cursor.
        await bot._wrapper(_room(), _event(ts=5))
        # Older / equal timestamp must be dropped.
        await bot._wrapper(_room(), _event(ts=5))
        await bot._wrapper(_room(), _event(ts=4))

        assert ran == [True]  # only the first ran
        # Framework only clears typing on the one event that actually
        # ran — replay-skipped events return before reaching the wrap.
        assert client.typing_calls == [("!r:server", False)]


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
