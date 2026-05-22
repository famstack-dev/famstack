"""RoomContext — the per-event view of a Matrix room.

The bot framework derives routing from a fresh RoomContext snapshot on
every event instead of pinning room ids at startup. The snapshot itself
is generic: room_id, alias, name, members, is_dm. Bot-specific routing
flags (e.g. archivist's "is this the documents room?") are built on
top of this snapshot by the subclass.

These tests cover the pure builder. Routing decisions that *use* the
context live in `test_archivist_routing.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))

from room_context import RoomContext, context_for, normalize_alias  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────

BOT = "@archivist-bot:home.local"


def _room(
    *,
    room_id: str = "!room:home.local",
    canonical_alias: str | None = None,
    name: str | None = None,
    members: list[str] | None = None,
):
    """Construct a duck-typed room object that matches what nio exposes.

    The real `MatrixRoom` carries dozens of fields we don't touch; only
    the four read by `context_for` matter, and `getattr` defaults take
    care of anything missing on a partially-populated room (which does
    happen in nio during the initial sync window).
    """
    return SimpleNamespace(
        room_id=room_id,
        canonical_alias=canonical_alias,
        name=name,
        users={uid: object() for uid in (members or [])},
    )


# ── Alias normalization ──────────────────────────────────────────────────

class TestNormalizeAlias:
    """Both ends of the docs-room match go through `normalize_alias`.
    The bot.toml setting is typically a bare local-part; the homeserver
    hands back the full `#name:server` form. Normalization gives us one
    canonical shape on both sides."""

    def test_strips_hash_and_server(self):
        assert normalize_alias("#documents:home.local") == "documents"

    def test_local_only_passes_through(self):
        assert normalize_alias("documents") == "documents"

    def test_whitespace_stripped(self):
        assert normalize_alias("  documents  ") == "documents"

    @pytest.mark.parametrize("v", [None, "", "   "])
    def test_empty_inputs_return_empty(self, v):
        assert normalize_alias(v) == ""


# ── Alias passthrough ────────────────────────────────────────────────────

class TestAliasField:
    """The alias is the routing key for any bot that wants to match a
    specific room type (documents room, notes room, etc.). The builder
    normalizes whatever the homeserver returns."""

    def test_alias_is_normalized_local_part(self):
        room = _room(canonical_alias="#documents:home.local")
        ctx = context_for(room, bot_user_id=BOT)
        assert ctx.alias == "documents"

    def test_missing_alias_collapses_to_empty(self):
        room = _room(canonical_alias=None)
        ctx = context_for(room, bot_user_id=BOT)
        assert ctx.alias == ""


# ── DM detection ─────────────────────────────────────────────────────────

class TestIsDM:
    """A room is treated as a DM when it has exactly two members and the
    bot is one of them. Matrix's `m.direct` account data would be more
    authoritative but nio doesn't surface it cleanly; the 2-member
    heuristic matches how Matrix clients tag DMs in practice."""

    def test_two_members_with_bot_is_dm(self):
        room = _room(members=[BOT, "@homer:home.local"])
        ctx = context_for(room, bot_user_id=BOT)
        assert ctx.is_dm is True

    def test_three_members_is_not_dm(self):
        room = _room(members=[BOT, "@homer:home.local", "@marge:home.local"])
        ctx = context_for(room, bot_user_id=BOT)
        assert ctx.is_dm is False

    def test_two_members_without_bot_is_not_dm(self):
        # A room with two humans and no bot can never be a DM with the bot.
        # In practice the bot can't see such rooms, but the predicate
        # should still hold.
        room = _room(members=["@homer:home.local", "@marge:home.local"])
        ctx = context_for(room, bot_user_id=BOT)
        assert ctx.is_dm is False

    def test_single_member_is_not_dm(self):
        # Bot alone in a room — happens briefly when the inviter leaves
        # before the bot finishes accepting.
        room = _room(members=[BOT])
        ctx = context_for(room, bot_user_id=BOT)
        assert ctx.is_dm is False


# ── Other fields ─────────────────────────────────────────────────────────

class TestContextFields:
    """The remaining context fields are passthroughs from the nio room.
    They exist so handlers and log lines don't have to reach into the
    nio object directly."""

    def test_room_id_passthrough(self):
        room = _room(room_id="!docs:home.local")
        ctx = context_for(room, bot_user_id=BOT)
        assert ctx.room_id == "!docs:home.local"

    def test_name_passthrough(self):
        room = _room(name="Family Documents")
        ctx = context_for(room, bot_user_id=BOT)
        assert ctx.name == "Family Documents"

    def test_missing_name_collapses_to_empty(self):
        room = _room(name=None)
        ctx = context_for(room, bot_user_id=BOT)
        assert ctx.name == ""

    def test_members_are_sorted(self):
        # nio backs `room.users` with a dict — order is insertion-dependent.
        # The context sorts for stable handling (logs, comparisons).
        room = _room(members=["@zoe:home.local", "@alice:home.local", BOT])
        ctx = context_for(room, bot_user_id=BOT)
        assert ctx.members == (
            "@alice:home.local", "@archivist-bot:home.local", "@zoe:home.local",
        )

    def test_empty_room_has_empty_members(self):
        room = _room(members=[])
        ctx = context_for(room, bot_user_id=BOT)
        assert ctx.members == ()

    def test_room_without_users_attr_tolerated(self):
        # A partially-constructed room object (e.g. straight after invite,
        # before initial sync) may lack `users`. The builder treats that
        # as an empty room rather than crashing the dispatcher.
        room = SimpleNamespace(room_id="!x", canonical_alias=None, name=None)
        ctx = context_for(room, bot_user_id=BOT)
        assert ctx.members == ()
        assert ctx.is_dm is False


# ── Immutability ─────────────────────────────────────────────────────────

class TestRoomContextIsFrozen:
    """Frozen so a handler that holds onto a context across awaits can't
    have its routing decision rewritten by an unrelated code path."""

    def test_cannot_mutate(self):
        room = _room(members=[BOT, "@homer:home.local"])
        ctx = context_for(room, bot_user_id=BOT)
        with pytest.raises((AttributeError, Exception)):  # FrozenInstanceError is a subclass
            ctx.is_dm = False  # type: ignore[misc]

    def test_is_dataclass_instance(self):
        room = _room()
        ctx = context_for(room, bot_user_id=BOT)
        assert isinstance(ctx, RoomContext)
