"""ScribeBot voice handling.

Scribe used to call nio's media download, `room_typing`, and a hand-
built `room_send` directly. After the transport consolidation it goes
through the framework: `_download_media` (authenticated endpoint),
`_set_typing`, and `_send` (formatted, threaded). These pin that the
handler drives the framework methods rather than the raw client.

The transcription HTTP call is delegated to the shared `Transcriber`
capability on the AI client; these tests inject a stub Transcriber so
the bot's wiring is exercised without a whisper server.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "messages" / "bot"))
# `lib/` hosts `stack.ai.client`, which the bot now imports at module load.
sys.path.insert(0, str(_REPO_ROOT / "lib"))

from scribe import ScribeBot  # noqa: E402
from stack.ai.client import LLMUnavailableError  # noqa: E402


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


class _StubTranscriber:
    """Stand-in for `stack.ai.client.Transcriber` — records calls and
    returns a configured transcript (or raises a configured error)."""

    def __init__(self, result: str = "hello world", error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[tuple[bytes, str]] = []

    async def transcribe(self, audio: bytes, *, filename: str = "voice.ogg",
                         model: str | None = None) -> str:
        self.calls.append((audio, filename))
        if self.error is not None:
            raise self.error
        return self.result


def _build(tmp_path, monkeypatch, *, transcriber: _StubTranscriber | None = None):
    # Transcriber.from_env reads WHISPER_URL at construction; we don't
    # care what URL the stub is "pointed at", but the constructor refuses
    # an empty value, so pin a benign placeholder.
    monkeypatch.setenv("WHISPER_URL", "http://test.local/v1")
    bot = ScribeBot(
        homeserver="http://x", user_id="@scribe-bot:server",
        password="x", session_dir=str(tmp_path),
    )
    bot._client = _FakeClient()
    bot._transcriber = transcriber or _StubTranscriber()
    return bot


@pytest.mark.asyncio
async def test_voice_routes_through_framework(tmp_path, monkeypatch):
    """A voice message is downloaded via `_download_media`, transcribed,
    and posted via `_send` threaded to the voice event; typing toggles
    via `_set_typing`."""
    bot = _build(tmp_path, monkeypatch)

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

    event = SimpleNamespace(
        sender="@homer:server", url="mxc://server/abc123",
        event_id="$v:server", body="voice.ogg",
    )
    await bot._on_voice(SimpleNamespace(room_id="!r:server"), event)

    assert calls["download"] == ["mxc://server/abc123"]
    # The Transcriber got the downloaded bytes and the caption filename.
    assert bot._transcriber.calls == [(b"audio-bytes", "voice.ogg")]
    assert len(calls["send"]) == 1
    room_id, text, reply_to = calls["send"][0]
    assert room_id == "!r:server"
    assert "hello world" in text
    assert reply_to == "$v:server"
    # Typing was driven via the framework helper (on at least once).
    assert ("!r:server", True) in calls["typing"]


@pytest.mark.asyncio
async def test_failed_download_sends_nothing(tmp_path, monkeypatch):
    """When media can't be downloaded, scribe bails without posting."""
    bot = _build(tmp_path, monkeypatch)
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
    # The Transcriber must not be called when there's nothing to transcribe.
    assert bot._transcriber.calls == []


@pytest.mark.asyncio
async def test_no_whisper_url_does_not_crash_construction(tmp_path, monkeypatch):
    """WHISPER_URL missing -> the bot still constructs, sets transcriber=None,
    and logs a warning. The family server should boot scribe even when AI
    hasn't been installed yet."""
    monkeypatch.delenv("WHISPER_URL", raising=False)
    bot = ScribeBot(
        homeserver="http://x", user_id="@scribe-bot:server",
        password="x", session_dir=str(tmp_path),
    )
    assert bot._transcriber is None


@pytest.mark.asyncio
async def test_voice_no_op_when_transcriber_disabled(tmp_path, monkeypatch):
    """Without a transcriber the bot silently ignores audio: no typing
    indicator, no apology reply, no download. The warning at startup is
    the one and only signal."""
    monkeypatch.delenv("WHISPER_URL", raising=False)
    bot = ScribeBot(
        homeserver="http://x", user_id="@scribe-bot:server",
        password="x", session_dir=str(tmp_path),
    )
    bot._client = _FakeClient()
    assert bot._transcriber is None

    calls = {"download": [], "send": [], "typing": []}

    async def fake_download(mxc):
        calls["download"].append(mxc)
        return b"audio-bytes"

    async def fake_send(room_id, text, reply_to=None, *, metadata=None):
        calls["send"].append(text)

    async def fake_typing(room_id, on=True):
        calls["typing"].append((room_id, on))

    bot._download_media = fake_download
    bot._send = fake_send
    bot._set_typing = fake_typing

    event = SimpleNamespace(
        sender="@homer:server", url="mxc://server/abc",
        event_id="$v:server", body="voice.ogg",
    )
    await bot._on_voice(SimpleNamespace(room_id="!r:server"), event)

    assert calls == {"download": [], "send": [], "typing": []}


@pytest.mark.asyncio
async def test_transcriber_error_falls_back_to_apology(tmp_path, monkeypatch):
    """If whisper is down (Transcriber raises LLMError) the bot tells the
    sender it couldn't do it — silent failure leaves people wondering."""
    failing = _StubTranscriber(error=LLMUnavailableError("whisper offline"))
    bot = _build(tmp_path, monkeypatch, transcriber=failing)
    sent: list[str] = []

    async def fake_download(mxc):
        return b"audio-bytes"

    async def fake_send(room_id, text, reply_to=None, *, metadata=None):
        sent.append(text)

    async def fake_typing(room_id, on=True):
        pass

    bot._download_media = fake_download
    bot._send = fake_send
    bot._set_typing = fake_typing

    event = SimpleNamespace(
        sender="@homer:server", url="mxc://server/abc",
        event_id="$v:server", body="voice.ogg",
    )
    await bot._on_voice(SimpleNamespace(room_id="!r:server"), event)

    assert len(sent) == 1
    assert "couldn't transcribe" in sent[0].lower()
