"""ArchivistBot._topic_binding: lazy bootstrap on first capture.

The orchestration glue between the pure topic_rooms helpers and the
capture pipeline. Reads `dev.famstack.capture` state from the room,
parses the room name if there's no state yet, and writes the state
before returning a TopicBinding for routing.

These tests use a light fake client that records state reads and
writes so we can pin the bootstrap without standing up matrix-nio.
The full ingest path is exercised by the e2e rig.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

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

    `room_get_state_event` returns either a SimpleNamespace with a
    `content` attribute or an object with no `content` (the no-state
    case). `room_put_state` records the write so tests can assert
    against it.
    """

    def __init__(self, *, initial_state: dict | None = None,
                 read_raises: bool = False, write_raises: bool = False):
        self._state = initial_state
        self._read_raises = read_raises
        self._write_raises = write_raises
        self.reads: list[tuple] = []
        self.writes: list[tuple] = []
        # nio's AsyncClient exposes `.rooms` as a dict; the room's
        # display name and member list ride on the Room object.
        self.rooms: dict = {}

    async def room_get_state_event(self, room_id, event_type, state_key=""):
        self.reads.append((room_id, event_type, state_key))
        if self._read_raises:
            raise RuntimeError("network burp")
        if self._state is None:
            return SimpleNamespace()  # no `content` attr => None
        return SimpleNamespace(content=self._state)

    async def room_put_state(self, room_id, event_type, content, state_key=""):
        if self._write_raises:
            raise RuntimeError("auth burp")
        self.writes.append((room_id, event_type, content, state_key))


def _bot(tmp_path, *, client: FakeStateClient | None = None) -> ArchivistBot:
    bot = ArchivistBot(
        homeserver="http://h", user_id=BOT_ID, password="x",
        session_dir=tmp_path,
    )
    if client is not None:
        bot._client = client  # bypass the framework's connect step
    return bot


def _room(*, room_id="!camping:server", name=None, members=()) -> SimpleNamespace:
    return SimpleNamespace(
        room_id=room_id,
        name=name,
        display_name=name,
        users={uid: object() for uid in members},
    )


# ── No state, no matching name ──────────────────────────────────────────


class TestNonTopicRoom:
    """A room with neither topic state nor a matching name returns
    None. Routing falls back to the sender's personal bucket; the
    capture proceeds with no seed and no bucket override."""

    @pytest.mark.asyncio
    async def test_plain_room_returns_none(self, tmp_path):
        client = FakeStateClient(initial_state=None)
        bot = _bot(tmp_path, client=client)
        room = _room(name="Family Chat", members=[BOT_ID, "@arthur:server"])
        binding = await bot._topic_binding(room, "@arthur:server")
        assert binding is None
        # No state was written -- nothing to bootstrap.
        assert client.writes == []

    @pytest.mark.asyncio
    async def test_empty_name_returns_none(self, tmp_path):
        client = FakeStateClient()
        bot = _bot(tmp_path, client=client)
        room = _room(name=None, members=[BOT_ID, "@arthur:server"])
        binding = await bot._topic_binding(room, "@arthur:server")
        assert binding is None


# ── Existing state ──────────────────────────────────────────────────────


class TestExistingTopicState:
    """When `dev.famstack.capture` state already carries kind=topic the
    archivist short-circuits the parse + write path and just returns
    the binding. Idempotent: re-bootstrap is a no-op."""

    SHARED_STATE = {
        "kind": "topic",
        "bucket": "camping",
        "slug": "camping",
        "display_name": "Camping",
        "default_topics": ["camping"],
        "scope": "shared",
        "extract_knowledge": True,
    }

    @pytest.mark.asyncio
    async def test_returns_binding_without_writing(self, tmp_path):
        client = FakeStateClient(initial_state=self.SHARED_STATE)
        bot = _bot(tmp_path, client=client)
        room = _room(name="Thema: Camping",
                     members=[BOT_ID, "@arthur:server", "@marge:server"])
        binding = await bot._topic_binding(room, "@arthur:server")
        assert binding is not None
        assert binding.bucket == "camping"
        assert binding.seed_topics == ["camping"]
        assert binding.scope == "shared"
        # Read once (to find existing state); no write.
        assert len(client.reads) == 1
        assert client.writes == []

    @pytest.mark.asyncio
    async def test_state_for_a_non_topic_kind_returns_none(self, tmp_path):
        """The room state event ID is shared with the existing
        capture-room and document-drop config; non-topic kinds must
        not match here."""
        client = FakeStateClient(initial_state={
            "kind": "capture", "extract_knowledge": True,
        })
        bot = _bot(tmp_path, client=client)
        room = _room(name="Family Chat", members=[BOT_ID, "@arthur:server"])
        binding = await bot._topic_binding(room, "@arthur:server")
        assert binding is None


# ── Bootstrap path ──────────────────────────────────────────────────────


class TestBootstrap:
    """No state yet + matching room name → archivist parses the name,
    counts humans, picks a scope, writes the state, and returns the
    binding. The next capture in the room reads the freshly written
    state and short-circuits."""

    @pytest.mark.asyncio
    async def test_shared_topic_bootstrap(self, tmp_path):
        """Two humans in the room → shared topic, bucket at root."""
        client = FakeStateClient(initial_state=None)
        bot = _bot(tmp_path, client=client)
        room = _room(
            name="Thema: Camping",
            members=[BOT_ID, "@arthur:server", "@marge:server"],
        )
        binding = await bot._topic_binding(room, "@arthur:server")
        assert binding is not None
        assert binding.bucket == "camping"
        assert binding.scope == "shared"
        assert binding.seed_topics == ["camping"]
        # State was written with the schema fields.
        assert len(client.writes) == 1
        room_id, event_type, content, _ = client.writes[0]
        assert event_type == "dev.famstack.capture"
        assert content["kind"] == "topic"
        assert content["bucket"] == "camping"
        assert content["scope"] == "shared"
        assert content["bootstrapped_by"] == "@arthur:server"

    @pytest.mark.asyncio
    async def test_personal_topic_bootstrap(self, tmp_path):
        """Solo room with the sender → personal topic nested under
        the sender's personal bucket."""
        client = FakeStateClient(initial_state=None)
        bot = _bot(tmp_path, client=client)
        room = _room(
            name="Thema: Gravel",
            members=[BOT_ID, "@arthur:server"],
        )
        binding = await bot._topic_binding(room, "@arthur:server")
        assert binding is not None
        assert binding.bucket == "arthur/gravel"
        assert binding.scope == "personal"
        content = client.writes[0][2]
        assert content["bucket"] == "arthur/gravel"
        assert content["scope"] == "personal"

    @pytest.mark.asyncio
    async def test_english_prefix_works_in_de_household(self, tmp_path):
        """`Topic:` is accepted regardless of `[core] language`."""
        client = FakeStateClient(initial_state=None)
        bot = _bot(tmp_path, client=client, )
        room = _room(name="Topic: Photography",
                     members=[BOT_ID, "@arthur:server", "@marge:server"])
        binding = await bot._topic_binding(room, "@arthur:server")
        assert binding is not None
        assert binding.slug == "photography"

    @pytest.mark.asyncio
    async def test_bootstrap_records_sender_provenance(self, tmp_path):
        client = FakeStateClient(initial_state=None)
        bot = _bot(tmp_path, client=client)
        room = _room(name="Thema: Camping",
                     members=[BOT_ID, "@arthur:server", "@marge:server"])
        await bot._topic_binding(room, "@arthur:server")
        content = client.writes[0][2]
        assert content["bootstrapped_by"] == "@arthur:server"
        # bootstrapped_at is an ISO timestamp ending in Z.
        assert content["bootstrapped_at"].endswith("Z")


# ── Reserved-slug refusal ──────────────────────────────────────────────


class TestReservedSlugRefusal:
    """A topic name whose slug collides with a vault built-in or the
    configured shared bucket is refused at bootstrap. The archivist
    logs and returns None; routing falls back to sender-based."""

    @pytest.mark.asyncio
    async def test_meta_slug_refused(self, tmp_path):
        client = FakeStateClient(initial_state=None)
        bot = _bot(tmp_path, client=client)
        room = _room(name="Thema: Meta",
                     members=[BOT_ID, "@arthur:server", "@marge:server"])
        binding = await bot._topic_binding(room, "@arthur:server")
        assert binding is None
        assert client.writes == []

    @pytest.mark.asyncio
    async def test_shared_bucket_name_refused(self, tmp_path):
        client = FakeStateClient(initial_state=None)
        bot = _bot(tmp_path, client=client, )
        # Default shared_bucket is "family".
        room = _room(name="Thema: Family",
                     members=[BOT_ID, "@arthur:server", "@marge:server"])
        binding = await bot._topic_binding(room, "@arthur:server")
        assert binding is None
        assert client.writes == []


# ── Resilience ─────────────────────────────────────────────────────────


class TestResilience:
    """The archivist must keep filing captures even when room state I/O
    misbehaves. State read failure is treated as no-state; state write
    failure leaves the binding live in memory for the current capture."""

    @pytest.mark.asyncio
    async def test_state_read_error_treats_as_no_state(self, tmp_path):
        """nio sometimes hands back errors instead of a clean 404. The
        bootstrap path must still kick in -- otherwise a transient
        read failure looks like a non-topic room and the first capture
        loses its seed."""
        client = FakeStateClient(initial_state=None, read_raises=True)
        bot = _bot(tmp_path, client=client)
        room = _room(name="Thema: Camping",
                     members=[BOT_ID, "@arthur:server", "@marge:server"])
        binding = await bot._topic_binding(room, "@arthur:server")
        # Read failed, so we treated it as no-state and bootstrapped.
        assert binding is not None
        assert binding.bucket == "camping"

    @pytest.mark.asyncio
    async def test_state_write_failure_still_returns_binding(self, tmp_path):
        """Forgejo/Synapse hiccups can fail the state write. The
        current capture still wants the bucket override + seed; the
        next capture will retry the write."""
        client = FakeStateClient(initial_state=None, write_raises=True)
        bot = _bot(tmp_path, client=client)
        room = _room(name="Thema: Camping",
                     members=[BOT_ID, "@arthur:server", "@marge:server"])
        binding = await bot._topic_binding(room, "@arthur:server")
        assert binding is not None
        assert binding.bucket == "camping"


# ── Human counting ─────────────────────────────────────────────────────


class TestHumanCounting:
    """Scope detection depends on filtering the bot out of the member
    list. The bot's own mxid never counts toward 'humans'."""

    def test_excludes_self(self, tmp_path):
        bot = _bot(tmp_path)
        room = _room(members=[BOT_ID, "@arthur:server"])
        assert bot._count_humans_in_room(room) == 1

    def test_no_members_is_zero(self, tmp_path):
        bot = _bot(tmp_path)
        room = _room(members=[])
        assert bot._count_humans_in_room(room) == 0

    def test_three_human_room(self, tmp_path):
        bot = _bot(tmp_path)
        room = _room(members=[BOT_ID, "@a:s", "@b:s", "@c:s"])
        assert bot._count_humans_in_room(room) == 3
