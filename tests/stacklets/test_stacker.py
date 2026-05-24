"""StackerBot reply formatting.

Stacker used to hand-roll markdown→HTML with regex in its own
`_send_reply`, bypassing the framework. After the transport
consolidation it routes through `MicroBot._send` — real markdown
(bold → <strong>, not the regex era's <b>), reply relation, single
`_room_send` seam. These pin that by driving a command against a
recording client and inspecting the sent content.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot"))

from stacker import StackerBot  # noqa: E402


class _FakeClient:
    def __init__(self):
        self.sends: list[tuple[str, str, dict]] = []

    def add_event_callback(self, cb, event_type):
        pass

    async def room_send(self, room_id, message_type, content):
        self.sends.append((room_id, message_type, content))


def _build(tmp_path) -> tuple[StackerBot, _FakeClient]:
    bot = StackerBot(
        homeserver="http://x", user_id="@stacker-bot:server",
        password="x", session_dir=str(tmp_path),
    )
    bot._client = _FakeClient()
    return bot, bot._client


def _text(body, *, sender="@homer:server", event_id="$e:server"):
    return SimpleNamespace(
        body=body, sender=sender, event_id=event_id, source={"content": {}},
    )


def _room(room_id="!r:server"):
    return SimpleNamespace(room_id=room_id)


class TestStackerReply:

    @pytest.mark.asyncio
    async def test_help_renders_real_markdown_and_threads(self, tmp_path):
        bot, client = _build(tmp_path)
        await bot._on_message(_room(), _text("stack help"))

        assert len(client.sends) == 1
        room_id, mtype, content = client.sends[0]
        assert room_id == "!r:server"
        assert mtype == "m.room.message"
        assert content["format"] == "org.matrix.custom.html"
        # Real markdown: bold → <strong>, inline code → <code>.
        assert "<strong>Available commands</strong>" in content["formatted_body"]
        assert "<code>stack status</code>" in content["formatted_body"]
        # The regex era emitted <b>; the framework path must not.
        assert "<b>" not in content["formatted_body"]
        # Threaded to the triggering message.
        assert content["m.relates_to"]["m.in_reply_to"]["event_id"] == "$e:server"

    @pytest.mark.asyncio
    async def test_non_command_is_ignored(self, tmp_path):
        bot, client = _build(tmp_path)
        await bot._on_message(_room(), _text("good morning everyone"))
        assert client.sends == []
