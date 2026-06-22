"""Unit tests for MicroBot.post_source_message — the twofold ingest helper.

The framework plumbing every ingest source shares: post a human-readable body
plus a `dev.famstack.source` block (raw_content + per-source fields), optionally
threaded under an `m.thread` root. No real Matrix — a fake client captures the
content dict.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNNER = Path(__file__).resolve().parent.parent.parent / "stacklets" / "core" / "bot-runner"
sys.path.insert(0, str(_RUNNER))

from microbot import MicroBot  # noqa: E402


class _Resp:
    def __init__(self, event_id):
        self.event_id = event_id


class _FakeClient:
    def __init__(self):
        self.sent: list[dict] = []

    async def room_send(self, room_id, message_type, content):
        self.sent.append({
            "room_id": room_id, "message_type": message_type, "content": content,
        })
        return _Resp("$evt123")


class _Bot(MicroBot):
    name = "test-bot"

    def register_callbacks(self, client):
        pass


def _bot(tmp_path) -> _Bot:
    b = _Bot(
        homeserver="http://hs", user_id="@t:hs", password="x",
        session_dir=str(tmp_path),
    )
    b._client = _FakeClient()
    return b


@pytest.mark.asyncio
async def test_posts_twofold_with_source_block(tmp_path):
    b = _bot(tmp_path)
    evt = await b.post_source_message(
        "!room:hs", body="**Hi**\n\nbody", source="email",
        raw_content="raw body",
        fields={"from": "a@b", "message_id": "<m1@h>", "thread_root": "<m1@h>"},
    )
    assert evt == "$evt123"
    content = b._client.sent[0]["content"]
    # Human face: the rendered body + HTML for rich clients.
    assert content["body"] == "**Hi**\n\nbody"
    assert content["msgtype"] == "m.text"
    assert "<strong>Hi</strong>" in content["formatted_body"]
    # Machine face: the source block with raw_content + per-source fields.
    src = content["dev.famstack.source"]
    assert src["source"] == "email"
    assert src["raw_content"] == "raw body"
    assert src["from"] == "a@b"
    assert src["message_id"] == "<m1@h>"
    # No thread root supplied → a plain (un-threaded) message.
    assert "m.relates_to" not in content


@pytest.mark.asyncio
async def test_threads_under_root_when_given(tmp_path):
    b = _bot(tmp_path)
    await b.post_source_message(
        "!r:hs", body="reply", source="email", raw_content="y",
        thread_root_event_id="$root1",
    )
    rel = b._client.sent[0]["content"]["m.relates_to"]
    assert rel["rel_type"] == "m.thread"
    assert rel["event_id"] == "$root1"
    # Reply fallback so non-threaded clients still show it in context.
    assert rel["m.in_reply_to"]["event_id"] == "$root1"


@pytest.mark.asyncio
async def test_returns_none_on_send_error(tmp_path):
    b = _bot(tmp_path)

    async def boom(**kwargs):
        raise RuntimeError("homeserver down")

    b._client.room_send = boom
    evt = await b.post_source_message(
        "!r:hs", body="x", source="email", raw_content="y",
    )
    assert evt is None
