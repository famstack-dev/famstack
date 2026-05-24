"""ScribeBot voice handling.

Scribe used to call nio's media download, `room_typing`, and a hand-
built `room_send` directly. After the transport consolidation it goes
through the framework: `_download_media` (authenticated endpoint),
`_set_typing`, and `_send` (formatted, threaded). These pin that the
handler drives the framework methods rather than the raw client.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "messages" / "bot"))

from scribe import ScribeBot  # noqa: E402


class _FakeClient:
    """Supports the legacy surface so pre-migration code runs to a clean
    assertion failure rather than crashing."""

    def __init__(self):
        self.sends: list[tuple] = []

    def add_event_callback(self, cb, event_type):
        pass

    async def room_typing(self, room_id, typing_state, timeout=None):
        pass

    async def room_send(self, room_id, message_type, content):
        self.sends.append((room_id, message_type, content))

    async def download(self, url):
        # Not a DownloadResponse — legacy path bails here, so the spies
        # below stay empty and the assertions fail cleanly (RED).
        return SimpleNamespace(body=b"")


def _build(tmp_path):
    bot = ScribeBot(
        homeserver="http://x", user_id="@scribe-bot:server",
        password="x", session_dir=str(tmp_path),
    )
    bot._client = _FakeClient()
    return bot


@pytest.mark.asyncio
async def test_voice_routes_through_framework(tmp_path, monkeypatch):
    """A voice message is downloaded via `_download_media`, transcribed,
    and posted via `_send` threaded to the voice event; typing toggles
    via `_set_typing`."""
    bot = _build(tmp_path)

    calls = {"download": [], "send": [], "typing": []}

    async def fake_download(mxc):
        calls["download"].append(mxc)
        return b"audio-bytes"

    async def fake_send(room_id, text, reply_to=None, *, metadata=None):
        calls["send"].append((room_id, text, reply_to))

    async def fake_typing(room_id, on=True):
        calls["typing"].append((room_id, on))

    bot._download_media = fake_download
    bot._send = fake_send
    bot._set_typing = fake_typing

    import scribe
    async def fake_transcribe(url, audio, filename):
        return "hello world"
    monkeypatch.setattr(scribe, "_transcribe", fake_transcribe)

    event = SimpleNamespace(
        sender="@homer:server", url="mxc://server/abc123",
        event_id="$v:server", body="voice.ogg",
    )
    await bot._on_voice(SimpleNamespace(room_id="!r:server"), event)

    assert calls["download"] == ["mxc://server/abc123"]
    assert len(calls["send"]) == 1
    room_id, text, reply_to = calls["send"][0]
    assert room_id == "!r:server"
    assert "hello world" in text
    assert reply_to == "$v:server"
    # Typing was driven via the framework helper (on at least once).
    assert ("!r:server", True) in calls["typing"]


@pytest.mark.asyncio
async def test_failed_download_sends_nothing(tmp_path):
    """When media can't be downloaded, scribe bails without posting."""
    bot = _build(tmp_path)
    sent = []

    async def fake_download(mxc):
        return None

    async def fake_send(room_id, text, reply_to=None, *, metadata=None):
        sent.append(text)

    async def fake_typing(room_id, on=True):
        pass

    bot._download_media = fake_download
    bot._send = fake_send
    bot._set_typing = fake_typing

    event = SimpleNamespace(
        sender="@homer:server", url="mxc://server/missing",
        event_id="$v:server", body="voice.ogg",
    )
    await bot._on_voice(SimpleNamespace(room_id="!r:server"), event)
    assert sent == []
