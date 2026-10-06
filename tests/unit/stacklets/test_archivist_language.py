"""The language the archivist files in, set per room.

The archivist writes titles, summaries and facts in the household
language (`[core] language`). A room can choose another one, or keep the
language of the source, with `!config language`:

  default             the household language (also: unset)
  de, en, es, ...     always this language
  source              the language the content is written in

The setting lives in the bot's room account data like every other room
option, so it is read through the same `!config` surface and survives
restarts. These tests drive the archivist from the chat side: a member
sets the option, then files something in that room.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from archivist import ArchivistBot  # noqa: E402
from test_microbot import _FakeHttp  # noqa: E402

ROOM = "!r:server"


@pytest.fixture
def bot(tmp_path, monkeypatch):
    """An archivist in a German household, with its room config stored in
    memory and its pipelines replaced by recorders."""
    monkeypatch.setenv("LANGUAGE", "de")
    calls: dict[str, list[dict]] = {"capture": [], "document": []}

    class _Capture:
        async def capture_binary(self, **kwargs):
            calls["capture"].append(kwargs)
            return SimpleNamespace(status="captured")

    class _Documents:
        async def process(self, **kwargs):
            calls["document"].append(kwargs)
            return SimpleNamespace(status="upload_failed", display_name="x", doc_id=None)

    bot = ArchivistBot(
        homeserver="http://homeserver", user_id="@archivist-bot:server",
        password="x", session_dir=tmp_path,
        services=SimpleNamespace(capture=_Capture(), pipeline=_Documents()),
    )
    bot._client = SimpleNamespace(access_token="tok", rooms={})
    bot._http = _FakeHttp()
    bot.sent = []

    async def _send(room_id, text, reply_to=None, **kw):
        bot.sent.append(text)

    async def _nothing(*_a, **_kw):
        return None

    bot._send = _send
    bot._react = _nothing
    bot._answer = _nothing
    bot._topic_binding = _nothing
    bot._reply_for_capture = _nothing
    bot.calls = calls
    return bot


async def _say(bot, body: str) -> bool:
    room = SimpleNamespace(room_id=ROOM)
    event = SimpleNamespace(body=body, event_id="$cfg", source={"content": {}})
    return await bot._maybe_handle_config_command(room, event)


async def _paste_image(bot) -> dict:
    await bot._handle_binary_capture(
        room_id=ROOM, file_data=b"\x89PNG\r\n", mime="image/png",
        filename="image.png", source_uri="mxc://home.test/x",
        sender_mxid="@homer:server", capture_id="$img",
    )
    return bot.calls["capture"][-1]


class TestSettingTheLanguage:

    @pytest.mark.asyncio
    async def test_a_room_can_choose_a_language(self, bot):
        assert await _say(bot, "!config language en")
        assert (await bot.get_room_config(ROOM))["language"] == "en"
        assert "English" in bot.sent[-1]

    @pytest.mark.asyncio
    async def test_the_overview_names_the_household_language(self, bot):
        """`!config` is the help. It says what "default" means here."""
        await _say(bot, "!config")
        overview = bot.sent[-1]
        assert "**language**: default" in overview
        assert "default: the household language (German)" in overview
        assert "source:" in overview and "en: English" in overview

    @pytest.mark.asyncio
    async def test_an_unknown_language_changes_nothing(self, bot):
        await _say(bot, "!config language klingon")
        assert "language" not in await bot.get_room_config(ROOM)
        assert "**language**" in bot.sent[-1], "the overview lists the valid values"


class TestFilingFollowsTheRoom:

    @pytest.mark.asyncio
    async def test_without_a_setting_the_household_language_applies(self, bot):
        assert (await _paste_image(bot))["write_in"] is None

    @pytest.mark.asyncio
    async def test_a_capture_is_written_in_the_rooms_language(self, bot):
        await _say(bot, "!config language en")
        assert (await _paste_image(bot))["write_in"] == "en"

    @pytest.mark.asyncio
    async def test_default_resets_the_room_to_the_household_language(self, bot):
        await _say(bot, "!config language en")
        await _say(bot, "!config language default")
        assert (await _paste_image(bot))["write_in"] is None

    @pytest.mark.asyncio
    async def test_a_room_can_keep_the_language_of_the_source(self, bot):
        await _say(bot, "!config language source")
        assert (await _paste_image(bot))["write_in"] == "source"

    @pytest.mark.asyncio
    async def test_a_document_is_filed_in_the_rooms_language(self, bot):
        await _say(bot, "!config language en")
        await bot._process_document(ROOM, "factura.pdf", "factura.pdf", b"%PDF-1.4")
        assert bot.calls["document"][-1]["write_in"] == "en"
