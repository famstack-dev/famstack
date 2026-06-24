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


class TestIsBotUser:
    """The framework's one definition of a bot account (localpart ends -bot),
    shared by member-counting, scope, and ignore-self loops."""

    def test_bot_accounts(self):
        assert MicroBot.is_bot_user("@mail-bot:simpson")
        assert MicroBot.is_bot_user("@archivist-bot:simpson")
        assert MicroBot.is_bot_user("scribe-bot")  # bare localpart

    def test_humans(self):
        assert not MicroBot.is_bot_user("@homer:simpson")
        assert not MicroBot.is_bot_user("@marge:simpson")

    def test_empty_or_none(self):
        assert not MicroBot.is_bot_user("")
        assert not MicroBot.is_bot_user(None)


class TestSyncDisplayName:
    """The bot applies bot.toml's display name to its Matrix profile on
    launch, so a rename reaches an already-provisioned account (account
    setup is skipped once a session exists)."""

    class _ProfileClient:
        def __init__(self, current):
            self._current = current
            self.set_calls: list[str] = []

        async def get_displayname(self, user_id):
            from types import SimpleNamespace
            return SimpleNamespace(displayname=self._current)

        async def set_displayname(self, name):
            self.set_calls.append(name)

    @pytest.mark.asyncio
    async def test_sets_when_different(self, tmp_path):
        b = _bot(tmp_path)
        b.display_name = "Mail Carrier"
        b._client = self._ProfileClient(current="Mail")
        await b._sync_display_name()
        assert b._client.set_calls == ["Mail Carrier"]

    @pytest.mark.asyncio
    async def test_skips_when_already_correct(self, tmp_path):
        b = _bot(tmp_path)
        b.display_name = "Mail Carrier"
        b._client = self._ProfileClient(current="Mail Carrier")
        await b._sync_display_name()
        assert b._client.set_calls == []

    @pytest.mark.asyncio
    async def test_noop_when_unset(self, tmp_path):
        b = _bot(tmp_path)
        b.display_name = None
        b._client = self._ProfileClient(current="whatever")
        await b._sync_display_name()
        assert b._client.set_calls == []
