"""Scribe retires itself.

Transcription moved into the transport, leaving this bot without a job.
It ships for one more release because the framework cannot deprovision a
bot: removing the declaration leaves the Matrix account joined to rooms
and answering nothing.

Scribe declared no room of its own, so the only affected installs are
those where someone invited it by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "stacklets" / "core" / "bot-runner"))
sys.path.insert(0, str(_ROOT / "stacklets" / "messages" / "bot"))
sys.path.insert(0, str(_ROOT / "lib"))

from scribe import ScribeBot  # noqa: E402


class _Room:
    def __init__(self, room_id):
        self.room_id = room_id


class _FakeClient:
    def __init__(self, *room_ids, leave_error: Exception | None = None):
        self.rooms = {r: _Room(r) for r in room_ids}
        self.sent: list[dict] = []
        self.left: list[str] = []
        self._leave_error = leave_error

    def add_event_callback(self, cb, event_type):
        pass

    async def room_send(self, room_id, message_type, content, **kw):
        self.sent.append({"room_id": room_id, "content": content})

    async def room_leave(self, room_id):
        if self._leave_error is not None:
            raise self._leave_error
        self.left.append(room_id)


def _bot(tmp_path, client) -> ScribeBot:
    bot = ScribeBot(homeserver="http://hs", user_id="@scribe-bot:simpson",
                    password="x", session_dir=str(tmp_path))
    bot._client = client
    return bot


class TestScribeRetires:
    """The one behaviour it has left."""

    @pytest.mark.asyncio
    async def test_it_says_goodbye_and_leaves_every_room(self, tmp_path):
        client = _FakeClient("!kitchen:simpson", "!notes:simpson")
        bot = _bot(tmp_path, client)

        await bot.retire_everywhere()

        assert client.left == ["!kitchen:simpson", "!notes:simpson"]
        assert [s["room_id"] for s in client.sent] == [
            "!kitchen:simpson", "!notes:simpson",
        ]

    @pytest.mark.asyncio
    async def test_the_goodbye_explains_itself(self, tmp_path):
        """The notice names what replaced the bot and confirms nothing
        needs setting up, so its departure is self-explanatory."""
        client = _FakeClient("!kitchen:simpson")
        bot = _bot(tmp_path, client)

        await bot.retire_everywhere()

        body = client.sent[0]["content"]["body"].lower()
        assert "voice" in body
        assert "automatic" in body or "automatically" in body
        # A notice, not a chat message: this is the machine talking.
        assert client.sent[0]["content"]["msgtype"] == "m.notice"

    @pytest.mark.asyncio
    async def test_it_leaves_a_room_it_is_freshly_invited_to(self, tmp_path):
        """An invite gets the same response as the boot sweep, so it
        does not leave a silent member behind."""
        client = _FakeClient("!new:simpson")
        bot = _bot(tmp_path, client)

        await bot.on_room_joined("!new:simpson")

        assert client.left == ["!new:simpson"]
        assert len(client.sent) == 1

    @pytest.mark.asyncio
    async def test_a_room_it_cannot_leave_does_not_stop_the_others(
        self, tmp_path,
    ):
        """A room that cannot be left is retried on the next launch, and
        must not block the rooms after it in this one."""
        client = _FakeClient("!stuck:simpson", "!fine:simpson",
                             leave_error=RuntimeError("homeserver said no"))
        bot = _bot(tmp_path, client)

        await bot.retire_everywhere()

        assert len(client.sent) == 2

    @pytest.mark.asyncio
    async def test_it_speaks_the_household_language(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LANGUAGE", "de")
        client = _FakeClient("!kueche:simpson")
        bot = _bot(tmp_path, client)

        await bot.retire_everywhere()

        assert "Sprachnachrichten" in client.sent[0]["content"]["body"]


class TestScribeAnswersNothing:
    """The bot registers no handlers and builds no transcriber. The
    framework performs the decode, and a second transcriber in the same
    process is what this release removed."""

    @pytest.mark.asyncio
    async def test_it_registers_no_message_handlers(self, tmp_path):
        # Async because `register_callbacks` schedules the retirement
        # sweep, and the framework always calls it from inside `start()`'s
        # running loop.
        client = _FakeClient()
        bot = _bot(tmp_path, client)
        bot.register_callbacks(client)
        assert bot._handlers == []

    def test_it_builds_no_transcriber_of_its_own(self, tmp_path, monkeypatch):
        """MicroBot builds one for the transport decode. Scribe does not
        use it and should never reach whisper."""
        monkeypatch.setenv("WHISPER_URL", "http://localhost:42062/v1")
        bot = _bot(tmp_path, _FakeClient())
        assert not hasattr(bot, "_scribe_transcriber")
        import scribe
        assert "Transcriber" not in scribe.__dict__
