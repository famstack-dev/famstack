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
    "account": "family",
    "folder": "INBOX",
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
    # Provenance tags: sender, mailbox, folder.
    st = call["seed_topics"]
    assert "Sender: office@school.example" in st
    assert "Mailbox: family" in st
    assert "Folder: INBOX" in st
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


# ── Bot-posted attachments (_on_file) ──────────────────────────────────────

def _file_event(content, *, sender="@mail-bot:server"):
    return SimpleNamespace(
        sender=sender, event_id="$f:server", server_timestamp=1,
        source={"content": content},
    )


def _wire_file(bot):
    """Stub _on_file's prerequisites and record the binary-capture call."""
    captured: dict = {}

    async def rec_binary(**kwargs):
        captured.update(kwargs)

    bot._handle_binary_capture = rec_binary
    bot._scan_sessions = set()
    bot._room_context = lambda room: SimpleNamespace(room_id=room.room_id)
    bot._send_room_welcome_if_needed = lambda *_a, **_k: _none()
    bot._is_bot_mentioned = lambda _e: False
    bot._should_react = lambda *_a, **_k: True
    bot._is_documents_room = lambda _ctx: False
    bot._download_media = lambda _url: _bytes()
    bot._send = lambda *_a, **_k: _none()
    bot._set_typing = lambda *_a, **_k: _none()
    return captured


async def _bytes():
    return b"%PDF-1.4 fake"


_ATTACH_CONTENT = {
    "msgtype": "m.file",
    "url": "mxc://server/abc",
    "filename": "slip.pdf",
    "body": "Permission slip",
    "info": {"mimetype": "application/pdf"},
    "dev.famstack.attachment": {
        "source": "email", "from": "office@school.example",
        "subject": "Permission slip", "message_id": "m@h", "thread_root": "m@h",
    },
}


@pytest.mark.asyncio
async def test_bot_attachment_files_without_bot_as_person(tmp_path):
    bot = _bot(tmp_path)
    captured = _wire_file(bot)
    await bot._on_file(_room(), _file_event(_ATTACH_CONTENT))
    assert captured["default_person"] is False
    assert "email" in captured["extra_seed_topics"]
    assert "Sender: office@school.example" in captured["extra_seed_topics"]


@pytest.mark.asyncio
async def test_unmarked_file_keeps_sender_attribution(tmp_path):
    # A human upload (no attachment marker) still attributes the sender.
    bot = _bot(tmp_path)
    captured = _wire_file(bot)
    content = {k: v for k, v in _ATTACH_CONTENT.items()
               if k != "dev.famstack.attachment"}
    await bot._on_file(_room(), _file_event(content, sender="@homer:server"))
    assert captured["default_person"] is True
    assert captured["extra_seed_topics"] is None
