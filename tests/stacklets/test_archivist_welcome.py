"""ArchivistBot per-room welcome: kind detection, gating, and content.

Every room the bot enters gets a context-aware welcome on first
encounter (documents / topic / personal / capture). The gate is a
`dev.famstack.welcome` state event the bot writes immediately after
posting; a re-encounter -- bot restart, reinvite, next event in the
same session -- reads the state and skips.

These tests pin the smaller seams (kind detection, text rendering,
state-event gating) plus the orchestrator end-to-end. The full ingest
path is exercised by the e2e rig; here we mock the nio surface so the
welcome behaviour stays testable without standing up Matrix.

Project rule: self-explaining UX. See
`memory/project_self_explaining_ux.md` for the principle these tests
guard.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from archivist import ArchivistBot  # noqa: E402


BOT_ID = "@archivist-bot:server"


# ── Fakes ────────────────────────────────────────────────────────────────


class FakeHistoryClient:
    """Minimal nio-shaped client for the own-messages welcome gate.

    `room_messages` returns a sender-filtered page the way Synapse
    would: `own_messages` own events when the bot has greeted the room
    before, an empty chunk when it has not. `raises` simulates a
    transient nio error.
    """

    def __init__(self, *, own_messages: int = 0, raises: bool = False):
        self._own = own_messages
        self._raises = raises
        self.queries: list[str] = []
        self.rooms: dict = {}

    async def room_messages(self, room_id, start=None, end=None,
                            direction=None, limit=10, message_filter=None):
        self.queries.append(room_id)
        if self._raises:
            raise RuntimeError("network burp")
        chunk = [SimpleNamespace(sender=BOT_ID) for _ in range(self._own)]
        return SimpleNamespace(chunk=chunk)


def _bot(tmp_path, *, client: FakeHistoryClient | None = None,
         documents_room_alias: str = "documents") -> ArchivistBot:
    bot = ArchivistBot(
        homeserver="http://h", user_id=BOT_ID, password="x",
        session_dir=tmp_path, documents_room_alias=documents_room_alias,
    )
    if client is not None:
        bot._client = client
    bot._send = AsyncMock()
    return bot


def _room(*, room_id="!room:server", name=None, canonical_alias=None,
          members=()) -> SimpleNamespace:
    return SimpleNamespace(
        room_id=room_id,
        name=name,
        display_name=name,
        canonical_alias=canonical_alias,
        users={uid: object() for uid in members},
    )


# ── Welcome kind detection ───────────────────────────────────────────────


class TestWelcomeKindFor:
    """`_welcome_kind_for` picks one of `topic`, `documents`, `personal`,
    `capture` based on the room's signals. Topic wins over everything
    else because the `Thema:` / `Topic:` prefix is the strongest
    declaration of user intent."""

    def test_topic_room_takes_precedence(self, tmp_path):
        bot = _bot(tmp_path)
        room = _room(
            name="Thema: Camping",
            canonical_alias="#documents:server",
            members=[BOT_ID, "@arthur:server", "@marge:server"],
        )
        ctx = bot._room_context(room)
        assert bot._welcome_kind_for(room, ctx) == "topic"

    def test_documents_room_detected(self, tmp_path):
        bot = _bot(tmp_path)
        room = _room(
            name="Documents",
            canonical_alias="#documents:server",
            members=[BOT_ID, "@arthur:server", "@marge:server"],
        )
        ctx = bot._room_context(room)
        assert bot._welcome_kind_for(room, ctx) == "documents"

    def test_dm_falls_back_to_personal(self, tmp_path):
        bot = _bot(tmp_path)
        room = _room(name="DM", members=[BOT_ID, "@arthur:server"])
        ctx = bot._room_context(room)
        assert bot._welcome_kind_for(room, ctx) == "personal"

    def test_generic_room_falls_back_to_capture(self, tmp_path):
        bot = _bot(tmp_path)
        room = _room(
            name="Family Chat",
            members=[BOT_ID, "@arthur:server", "@marge:server"],
        )
        ctx = bot._room_context(room)
        assert bot._welcome_kind_for(room, ctx) == "capture"

    def test_english_topic_prefix_also_detected(self, tmp_path):
        bot = _bot(tmp_path)
        room = _room(
            name="Topic: Photography",
            members=[BOT_ID, "@arthur:server", "@marge:server"],
        )
        ctx = bot._room_context(room)
        assert bot._welcome_kind_for(room, ctx) == "topic"


# ── Welcome text rendering ───────────────────────────────────────────────


class TestWelcomeTextFor:
    """Each welcome variant fills in the right context variables -- the
    topic display name in the topic welcome, the Paperless URL in the
    documents welcome -- so the message reads as something the room
    is actually for."""

    def test_topic_welcome_carries_display_and_slug(self, tmp_path):
        bot = _bot(tmp_path)
        room = _room(
            name="Thema: Van Life",
            members=[BOT_ID, "@arthur:server", "@marge:server"],
        )
        ctx = bot._room_context(room)
        text = bot._welcome_text_for(room, ctx)
        assert "Van Life" in text
        assert "`van-life`" in text  # slug appears in the tag explanation

    def test_topic_welcome_carries_shared_bucket_path(self, tmp_path):
        """Two-or-more humans → shared scope → bucket is
        `<shared_bucket>/<slug>/`. The welcome doubles as a Forgejo
        navigation hint."""
        bot = _bot(tmp_path)
        room = _room(
            name="Thema: Camping",
            members=[BOT_ID, "@arthur:server", "@marge:server"],
        )
        ctx = bot._room_context(room)
        text = bot._welcome_text_for(room, ctx)
        assert "family/camping" in text

    def test_topic_welcome_carries_personal_bucket_path(self, tmp_path):
        """One human in the room → personal scope → bucket is
        `<localpart>/<slug>/`. The bucket hint points at the family
        member's own folder."""
        bot = _bot(tmp_path)
        room = _room(
            name="Thema: Gravel",
            members=[BOT_ID, "@arthur:server"],
        )
        ctx = bot._room_context(room)
        text = bot._welcome_text_for(room, ctx)
        assert "arthur/gravel" in text

    def test_documents_welcome_carries_paperless_url(self, tmp_path):
        """The documents welcome ends with a link the user can click
        to land in the Paperless web UI."""
        bot = _bot(tmp_path, documents_room_alias="documents")
        bot.paperless_public_url = "http://paperless.test"
        room = _room(
            name="Documents",
            canonical_alias="#documents:server",
            members=[BOT_ID, "@arthur:server", "@marge:server"],
        )
        ctx = bot._room_context(room)
        text = bot._welcome_text_for(room, ctx)
        assert "http://paperless.test" in text

    def test_personal_welcome_renders(self, tmp_path):
        bot = _bot(tmp_path)
        room = _room(name="DM", members=[BOT_ID, "@arthur:server"])
        ctx = bot._room_context(room)
        text = bot._welcome_text_for(room, ctx)
        # Variant-specific marker: the DM-shaped welcome calls itself
        # the user's personal capture surface.
        assert "personal" in text.lower()

    def test_capture_welcome_mentions_topic_pro_tip(self, tmp_path):
        """The fallback welcome teaches the user how to escalate a
        generic room into a topic room via the `Thema:` prefix --
        self-explanation cascade."""
        bot = _bot(tmp_path)
        room = _room(
            name="Family Chat",
            members=[BOT_ID, "@arthur:server", "@marge:server"],
        )
        ctx = bot._room_context(room)
        text = bot._welcome_text_for(room, ctx)
        assert "Thema:" in text


# ── Per-room welcome gate ────────────────────────────────────────────────


class TestWelcomeGate:
    """`_send_room_welcome_if_needed` posts once per room. The gate is
    the bot's own message history (a read needs no power level, so it
    works in user-created rooms where the bot cannot write state), and
    an in-memory cache keeps it to one history query per room per
    process lifetime."""

    @pytest.mark.asyncio
    async def test_fresh_room_gets_welcome(self, tmp_path):
        client = FakeHistoryClient(own_messages=0)
        bot = _bot(tmp_path, client=client)
        room = _room(
            name="Documents",
            canonical_alias="#documents:server",
            members=[BOT_ID, "@arthur:server", "@marge:server"],
        )
        ctx = bot._room_context(room)
        await bot._send_room_welcome_if_needed(room, ctx)
        bot._send.assert_called_once()
        assert client.queries == ["!room:server"]

    @pytest.mark.asyncio
    async def test_room_with_own_history_skipped(self, tmp_path):
        """The bot greeted this room in a previous run -- its own
        message is in the history, so a restart stays silent."""
        client = FakeHistoryClient(own_messages=1)
        bot = _bot(tmp_path, client=client)
        room = _room(
            name="Documents",
            canonical_alias="#documents:server",
            members=[BOT_ID, "@arthur:server", "@marge:server"],
        )
        ctx = bot._room_context(room)
        await bot._send_room_welcome_if_needed(room, ctx)
        bot._send.assert_not_called()

    @pytest.mark.asyncio
    async def test_history_check_failure_still_welcomes(self, tmp_path):
        """A transient nio failure on the history read should NOT
        silently hide the welcome from a brand-new room -- the gate
        fails open and posts."""
        client = FakeHistoryClient(raises=True)
        bot = _bot(tmp_path, client=client)
        room = _room(
            name="Documents",
            canonical_alias="#documents:server",
            members=[BOT_ID, "@arthur:server", "@marge:server"],
        )
        ctx = bot._room_context(room)
        await bot._send_room_welcome_if_needed(room, ctx)
        bot._send.assert_called_once()

    @pytest.mark.asyncio
    async def test_second_encounter_uses_cache_not_history(self, tmp_path):
        """Within one session the second event in the same room must
        neither re-welcome nor re-query the history. This is the
        production regression: every message in a user-created topic
        room produced a fresh welcome."""
        client = FakeHistoryClient(own_messages=0)
        bot = _bot(tmp_path, client=client)
        room = _room(
            name="Thema: Camping",
            members=[BOT_ID, "@arthur:server", "@marge:server"],
        )
        ctx = bot._room_context(room)
        await bot._send_room_welcome_if_needed(room, ctx)
        await bot._send_room_welcome_if_needed(room, ctx)
        bot._send.assert_called_once()
        assert client.queries == ["!room:server"]

    @pytest.mark.asyncio
    async def test_skipped_room_is_cached_too(self, tmp_path):
        """A room recognized as already-welcomed via history is cached
        the same way -- one query, then silence."""
        client = FakeHistoryClient(own_messages=1)
        bot = _bot(tmp_path, client=client)
        room = _room(
            name="Documents",
            canonical_alias="#documents:server",
            members=[BOT_ID, "@arthur:server", "@marge:server"],
        )
        ctx = bot._room_context(room)
        await bot._send_room_welcome_if_needed(room, ctx)
        await bot._send_room_welcome_if_needed(room, ctx)
        bot._send.assert_not_called()
        assert client.queries == ["!room:server"]
