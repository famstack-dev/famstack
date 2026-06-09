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


class FakeStateClient:
    """Minimal nio-shaped client for room state reads and writes.

    Mirrors the topic-bootstrap test fake but tracks reads and writes
    keyed by event type so welcome state and topic state can coexist
    in the same test bot without interfering.
    """

    def __init__(self, *, initial_state: dict | None = None,
                 read_raises: bool = False, write_raises: bool = False):
        self._states: dict[tuple[str, str], dict | None] = {}
        if initial_state is not None:
            self._states[("!room:server", "dev.famstack.welcome")] = initial_state
        self._read_raises = read_raises
        self._write_raises = write_raises
        self.reads: list[tuple] = []
        self.writes: list[tuple] = []
        self.rooms: dict = {}

    async def room_get_state_event(self, room_id, event_type, state_key=""):
        self.reads.append((room_id, event_type, state_key))
        if self._read_raises:
            raise RuntimeError("network burp")
        state = self._states.get((room_id, event_type))
        if state is None:
            return SimpleNamespace()  # no `content` attr → None
        return SimpleNamespace(content=state)

    async def room_put_state(self, room_id, event_type, content, state_key=""):
        if self._write_raises:
            raise RuntimeError("auth burp")
        self._states[(room_id, event_type)] = content
        self.writes.append((room_id, event_type, content, state_key))


def _bot(tmp_path, *, client: FakeStateClient | None = None,
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


# ── Per-room state gate ─────────────────────────────────────────────────


class TestWelcomeStateGate:
    """`_send_room_welcome_if_needed` posts once, marks the room as
    welcomed, and short-circuits on every subsequent call. The state
    event is the single source of truth -- not an in-memory flag --
    so the gate survives bot restarts."""

    @pytest.mark.asyncio
    async def test_fresh_room_gets_welcome(self, tmp_path):
        client = FakeStateClient(initial_state=None)
        bot = _bot(tmp_path, client=client)
        room = _room(
            name="Documents",
            canonical_alias="#documents:server",
            members=[BOT_ID, "@arthur:server", "@marge:server"],
        )
        ctx = bot._room_context(room)
        await bot._send_room_welcome_if_needed(room, ctx)
        bot._send.assert_called_once()
        # State was written so the next encounter is silent.
        assert len(client.writes) == 1
        room_id, event_type, content, _ = client.writes[0]
        assert event_type == "dev.famstack.welcome"
        assert content["bot"] == bot.name
        assert content["kind"] == "documents"
        assert "welcomed_at" in content

    @pytest.mark.asyncio
    async def test_already_welcomed_room_skipped(self, tmp_path):
        existing = {
            "bot": "archivist-bot",
            "kind": "documents",
            "welcomed_at": "2026-06-01T12:00:00Z",
        }
        client = FakeStateClient(initial_state=existing)
        bot = _bot(tmp_path, client=client)
        room = _room(
            name="Documents",
            canonical_alias="#documents:server",
            members=[BOT_ID, "@arthur:server", "@marge:server"],
        )
        ctx = bot._room_context(room)
        await bot._send_room_welcome_if_needed(room, ctx)
        bot._send.assert_not_called()
        assert client.writes == []

    @pytest.mark.asyncio
    async def test_read_failure_treats_as_no_state(self, tmp_path):
        """A transient nio read failure should NOT silently hide the
        welcome from a brand-new room -- the gate read is best-effort
        and falls through to post on failure, the way the topic-room
        bootstrap does."""
        client = FakeStateClient(initial_state=None, read_raises=True)
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
    async def test_write_failure_still_posts_welcome(self, tmp_path):
        """Forgejo / Synapse hiccups can fail the state write. The
        welcome itself still goes out; the next encounter reposts
        (rare, recoverable). Better the duplicate than the silence."""
        client = FakeStateClient(initial_state=None, write_raises=True)
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
    async def test_welcome_kind_recorded_in_state(self, tmp_path):
        """The state event records which variant was sent. A future
        deriver or analytics surface can reason about how each room
        was introduced without re-running the kind detection."""
        client = FakeStateClient(initial_state=None)
        bot = _bot(tmp_path, client=client)
        room = _room(
            name="Thema: Camping",
            members=[BOT_ID, "@arthur:server", "@marge:server"],
        )
        ctx = bot._room_context(room)
        await bot._send_room_welcome_if_needed(room, ctx)
        content = client.writes[0][2]
        assert content["kind"] == "topic"
