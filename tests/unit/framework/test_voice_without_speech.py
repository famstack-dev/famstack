"""A voice message with speech off gets one answer, from one bot.

With no transcriber configured (`[ai] speech = false`) a voice message
cannot be read. The framework gives it to no handler, so a person would
think it was filed. The room's archivist, the one bot that takes
captures, says so once and names how to turn speech on. Other bots in
the room stay silent. With speech on nothing changes.

The bots are driven through `_dispatch`, as the bot runner calls them.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from nio.events.room_events import RoomMessage

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

from stack.bot import MicroBot  # noqa: E402
from stacklets.docs.bot.archivist import ArchivistBot  # noqa: E402

ROOM_ID = "!kitchen:simpson"


def _voice(thread_root=None):
    content = {"msgtype": "m.audio", "body": "Voice message",
               "url": "mxc://simpson/abc",
               "info": {"mimetype": "audio/ogg", "duration": 4200}}
    if thread_root:
        content["m.relates_to"] = {"rel_type": "m.thread", "event_id": thread_root}
    return RoomMessage.parse_event({
        "type": "m.room.message", "event_id": "$voice1", "sender": "@homer:simpson",
        "origin_server_ts": 1700000000000, "content": content,
    })


class _Client:
    def __init__(self):
        room = SimpleNamespace(
            room_id=ROOM_ID, canonical_alias="#kitchen:simpson", name="Kitchen",
            users={u: object() for u in ("@archivist-bot:simpson", "@homer:simpson")},
        )
        self.rooms = {ROOM_ID: room}

    async def room_typing(self, *a, **kw):
        pass


class _OtherBot(MicroBot):
    name = "other-bot"

    def register_callbacks(self, client):
        pass


def _wire(bot, tmp_path, *, transcriber=None):
    bot._client = _Client()
    bot._transcriber = transcriber
    bot.sent = []

    async def _send(room_id, text, reply_to=None, **kw):
        bot.sent.append((room_id, text, reply_to))

    async def _config(room_id):
        return {}

    bot._send = _send
    bot.get_room_config = _config
    return bot


def _archivist(tmp_path, **kw):
    bot = ArchivistBot(homeserver="http://hs", user_id="@archivist-bot:simpson",
                       password="x", session_dir=tmp_path)
    return _wire(bot, tmp_path, **kw)


class _Transcriber:
    async def transcribe(self, audio, **kw):
        return "buy milk"


@pytest.mark.asyncio
async def test_speech_off_the_capture_bot_answers_once(tmp_path):
    bot = _archivist(tmp_path)
    await bot._dispatch(ROOM_ID, _voice())

    assert len(bot.sent) == 1
    room_id, text, reply_to = bot.sent[0]
    assert room_id == ROOM_ID and reply_to == "$voice1"
    assert "voice" in text.lower() and "speech = true" in text


@pytest.mark.asyncio
async def test_speech_off_another_bot_in_the_room_stays_silent(tmp_path):
    other = _wire(_OtherBot(homeserver="http://hs", user_id="@other-bot:simpson",
                            password="x", session_dir=tmp_path), tmp_path)
    await other._dispatch(ROOM_ID, _voice())

    assert other.sent == []


@pytest.mark.asyncio
async def test_speech_off_a_voice_reply_in_another_bots_thread_gets_no_answer(tmp_path):
    bot = _archivist(tmp_path)

    async def _owner(room_id, root):
        return "@mail-bot:simpson"

    bot.thread_owner = _owner
    await bot._dispatch(ROOM_ID, _voice(thread_root="$mailcard"))

    assert bot.sent == []


@pytest.mark.asyncio
async def test_speech_off_the_memories_room_gets_no_answer(tmp_path):
    bot = _archivist(tmp_path)
    bot._diary.is_room = lambda ctx: True
    await bot._dispatch(ROOM_ID, _voice(thread_root="$diarycard"))

    assert bot.sent == []


@pytest.mark.asyncio
async def test_speech_on_nothing_is_said_about_speech(tmp_path):
    bot = _archivist(tmp_path, transcriber=_Transcriber())

    async def _download(url):
        return b"audio"

    bot._download_media = _download
    await bot._dispatch(ROOM_ID, _voice())

    assert not any("speech = true" in text for _, text, _ in bot.sent)
