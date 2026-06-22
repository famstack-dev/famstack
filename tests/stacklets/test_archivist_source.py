"""ArchivistBot consumes `dev.famstack.source` messages (the mail bot's posts).

The mail bot posts a twofold source event into a room; the archivist recognises
it and folds it through `capture_email` — the same capture path a pasted URL
takes. These tests poke `_on_text` with a fake event carrying the source block
and a fake capture pipeline, no Matrix or IMAP.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "lib"))
sys.path.insert(0, str(_REPO / "stacklets" / "core" / "bot-runner"))
sys.path.insert(0, str(_REPO / "stacklets" / "docs" / "bot"))

from archivist import ArchivistBot  # noqa: E402

BOT_ID = "@archivist-bot:server"


def _bot(tmp_path):
    return ArchivistBot(
        homeserver="http://hs", user_id=BOT_ID, password="x", session_dir=tmp_path,
    )


class _FakeCapture:
    def __init__(self, *, envelope=True):
        self.calls: list[dict] = []
        self._envelope = {"type": "capture.filed"} if envelope else None

    async def capture_email(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            status="captured", vault_path="family/emails/2026/06/x.md",
            classification={"title": "Elternabend"}, envelope=self._envelope,
        )


def _source_event(block, *, sender="@mail-bot:server"):
    return SimpleNamespace(
        body="**Elternabend**\nfrom o@s\n\nbody text",
        sender=sender, event_id="$e:server", server_timestamp=1,
        source={"content": {"dev.famstack.source": block}},
    )


def _room():
    return SimpleNamespace(room_id="!post:server", canonical_alias="#post:server",
                           name=None, users={})


_EMAIL_BLOCK = {
    "source": "email",
    "raw_content": "Bitte das Formular bis Freitag zurueck.",
    "from": "office@school.example",
    "subject": "Elternabend",
    "message_id": "root@school.example",
    "thread_root": "root@school.example",
    "captured_at": "2026-06-21",
}


def _wire(bot):
    sends: list[dict] = []

    async def _record_send(room_id, text, reply_to=None, *, metadata=None):
        sends.append({"room_id": room_id, "text": text, "reply_to": reply_to,
                      "metadata": metadata})

    bot._send = _record_send
    bot._send_room_welcome_if_needed = lambda *_a, **_kw: _none()
    return sends


async def _none():
    return None


@pytest.mark.asyncio
async def test_email_source_folds_through_capture(tmp_path):
    bot = _bot(tmp_path)
    cap = _FakeCapture()
    bot._capture = cap
    sends = _wire(bot)

    await bot._on_text(_room(), _source_event(_EMAIL_BLOCK))

    assert len(cap.calls) == 1
    call = cap.calls[0]
    assert call["body"] == "Bitte das Formular bis Freitag zurueck."
    assert call["subject"] == "Elternabend"
    assert call["message_id"] == "root@school.example"
    assert call["thread_root"] == "root@school.example"
    assert call["from_addr"] == "office@school.example"
    assert call["captured_at"] == "2026-06-21"
    # Email files to the institutional bucket, like documents.
    assert call["bucket"] == bot.shared_bucket
    # The filing envelope rides onto the timeline for the deriver/reprocess.
    assert sends and sends[0]["metadata"]["dev.famstack.event"] == {"type": "capture.filed"}


@pytest.mark.asyncio
async def test_non_bot_sender_is_rejected(tmp_path):
    bot = _bot(tmp_path)
    cap = _FakeCapture()
    bot._capture = cap
    _wire(bot)
    # A family member cannot spoof an ingest event.
    await bot._on_text(_room(), _source_event(_EMAIL_BLOCK, sender="@homer:server"))
    assert cap.calls == []


@pytest.mark.asyncio
async def test_plain_bot_chatter_is_ignored(tmp_path):
    # The mail bot's join welcome (a plain message from a -bot sender, no
    # source block) must not be treated as a capture or query.
    bot = _bot(tmp_path)
    cap = _FakeCapture()
    bot._capture = cap
    sends = _wire(bot)
    welcome = SimpleNamespace(
        body="mail bot here. I'll deliver new email into this room",
        sender="@mail-bot:server", event_id="$w:server", server_timestamp=1,
        source={"content": {}},  # no dev.famstack.source
    )
    await bot._on_text(_room(), welcome)
    assert cap.calls == []
    assert sends == []


@pytest.mark.asyncio
async def test_non_email_source_ignored(tmp_path):
    bot = _bot(tmp_path)
    cap = _FakeCapture()
    bot._capture = cap
    _wire(bot)
    block = {"source": "webhook", "raw_content": "x"}
    await bot._on_text(_room(), _source_event(block))
    assert cap.calls == []


@pytest.mark.asyncio
async def test_empty_raw_content_skipped(tmp_path):
    bot = _bot(tmp_path)
    cap = _FakeCapture()
    bot._capture = cap
    _wire(bot)
    block = {**_EMAIL_BLOCK, "raw_content": "   "}
    await bot._on_text(_room(), _source_event(block))
    assert cap.calls == []


@pytest.mark.asyncio
async def test_no_envelope_skips_timeline_post(tmp_path):
    bot = _bot(tmp_path)
    cap = _FakeCapture(envelope=False)
    bot._capture = cap
    sends = _wire(bot)
    await bot._on_text(_room(), _source_event(_EMAIL_BLOCK))
    assert len(cap.calls) == 1  # still filed
    assert sends == []          # but nothing to put on the timeline
