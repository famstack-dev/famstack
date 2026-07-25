"""Room-context + addressing primitives against real Synapse.

The framework routes every event off live nio room/event state:

  - is this a DM (a 2-member room the bot is in)?
  - did the user @-mention the bot (MSC3952 m.mentions, or the mxid
    in the body)?
  - is this a reply to one of the bot's own messages, and what envelope
    rode on it?

The offline unit tests (test_archivist_routing.py, test_microbot.py)
pin this logic with SimpleNamespace fakes. These confirm the SAME
predicates hold on the objects Synapse actually produces — real
membership lists, real m.mentions, real m.relates_to — which a fake
can quietly diverge from.

The "bot" here is a logged-in family member (Bart); the other members
(Homer, Lisa) play the humans. Run on a clean instance:

    tests/integration/stacktests pytest tests/integration/test_room_context_e2e.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from nio import AsyncClient, RoomInviteResponse
from nio.api import RoomVisibility

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))

from microbot import MicroBot  # noqa: E402
from tests.integration.matrix import wait_for_room_event  # noqa: E402


class _Bot(MicroBot):
    name = "room-context-test-bot"

    def register_callbacks(self, c):
        return None


def _client_from(creds) -> AsyncClient:
    client = AsyncClient(creds.homeserver, creds.user_id)
    client.access_token = creds.access_token
    client.device_id = creds.device_id
    client.user_id = creds.user_id
    return client


@pytest.fixture
async def family(matrix):
    """nio AsyncClients for the whole Simpsons family, keyed by localpart.

    Function-scoped so each test gets fresh sync state; closed on teardown.
    """
    clients = {u: _client_from(c) for u, c in matrix.items()}
    try:
        yield clients
    finally:
        for c in clients.values():
            await c.close()


def _bot_over(client) -> MicroBot:
    bot = _Bot(client.homeserver, client.user_id, "x", "/tmp")
    bot._client = client
    return bot


async def _room_with_members(host, others: list[AsyncClient]) -> str:
    """Host creates a private room and the `others` join it."""
    create = await host.room_create(
        name="room-context-probe", visibility=RoomVisibility.private,
    )
    room_id = getattr(create, "room_id", None)
    assert room_id, f"room_create failed: {create}"
    for guest in others:
        invite = await host.room_invite(room_id, guest.user_id)
        assert isinstance(invite, RoomInviteResponse), f"invite failed: {invite}"
        await guest.join(room_id)
    return room_id


async def _await_room(client, room_id: str, *, members: int, tries: int = 12):
    """Sync until the bot's view of the room has `members` joined users.

    Membership propagates over a sync or two after invites/joins; poll
    full_state until the count settles so DM detection sees ground truth.
    """
    import asyncio
    room = None
    for _ in range(tries):
        await client.sync(timeout=2000, full_state=True)
        room = client.rooms.get(room_id)
        if room is not None and len(room.users) >= members:
            return room
        await asyncio.sleep(1)
    return room


async def _find_event(client, room_id: str, body: str):
    return await wait_for_room_event(
        client,
        room_id,
        lambda e: getattr(e, "body", None) == body,
        timeout=8.0,
    )


# ── is_dm ─────────────────────────────────────────────────────────────────


async def test_two_member_room_is_dm(family):
    """Bot + one human → is_dm True; members is the sorted pair."""
    bart, homer = family["bart"], family["homer"]
    bot = _bot_over(bart)

    room_id = await _room_with_members(bart, [homer])
    room = await _await_room(bart, room_id, members=2)

    ctx = bot._room_context(room)
    assert ctx.is_dm is True
    assert ctx.members == tuple(sorted([bart.user_id, homer.user_id]))


async def test_three_member_room_is_not_dm(family):
    """Bot + two humans → not a DM; routing falls to room-mode rules."""
    bart, homer, lisa = family["bart"], family["homer"], family["lisa"]
    bot = _bot_over(bart)

    room_id = await _room_with_members(bart, [homer, lisa])
    room = await _await_room(bart, room_id, members=3)

    ctx = bot._room_context(room)
    assert ctx.is_dm is False
    assert len(ctx.members) == 3


# ── @-mention detection ─────────────────────────────────────────────────


async def test_at_mention_via_m_mentions_detected(family):
    """A modern client populates m.mentions.user_ids; the bot reads it
    off the real event and treats itself as addressed."""
    bart, homer = family["bart"], family["homer"]
    bot = _bot_over(bart)

    room_id = await _room_with_members(bart, [homer])
    await _await_room(bart, room_id, members=2)

    await homer.room_send(
        room_id, "m.room.message",
        {
            "msgtype": "m.text",
            "body": "please search for the Duff Insurance invoice",
            "m.mentions": {"user_ids": [bart.user_id]},
        },
    )
    event = await _find_event(bart, room_id, "please search for the Duff Insurance invoice")
    assert event is not None, "mention message never arrived"
    assert bot._is_bot_mentioned(event) is True


async def test_plain_message_is_not_a_mention(family):
    """A normal message in the room is not an address — no m.mentions,
    no mxid in the body."""
    bart, homer = family["bart"], family["homer"]
    bot = _bot_over(bart)

    room_id = await _room_with_members(bart, [homer])
    await _await_room(bart, room_id, members=2)

    await homer.room_send(
        room_id, "m.room.message", {"msgtype": "m.text", "body": "morning everyone"},
    )
    event = await _find_event(bart, room_id, "morning everyone")
    assert event is not None
    assert bot._is_bot_mentioned(event) is False


# ── reply-to-our-message behavior ─────────────────────────────────────────


async def test_reply_to_our_filing_resolves_envelope(family):
    """Homer replies to a filing the bot posted; the bot resolves the
    reply back to the envelope (the real reply-to-reprocess trigger)."""
    bart, homer = family["bart"], family["homer"]
    bot = _bot_over(bart)

    room_id = await _room_with_members(bart, [homer])
    await _await_room(bart, room_id, members=2)
    await _await_room(homer, room_id, members=2)

    # The bot files a document, riding the envelope on the message.
    await bot._send(
        room_id, "Filed: Duff Insurance invoice (#5)",
        metadata={"dev.famstack.event": {
            "type": "document.filed", "data": {"paperless_id": 5},
        }},
    )
    filing = await _find_event(homer, room_id, "Filed: Duff Insurance invoice (#5)")
    assert filing is not None, "bot filing never reached Homer"

    # Homer replies with a correction.
    await homer.room_send(
        room_id, "m.room.message",
        {
            "msgtype": "m.text",
            "body": "this is insurance, not a utility bill",
            "m.relates_to": {"m.in_reply_to": {"event_id": filing.event_id}},
        },
    )
    reply = await _find_event(bart, room_id, "this is insurance, not a utility bill")
    assert reply is not None, "Homer's reply never reached the bot"

    envelope = await bot._reply_parent_envelope(room_id, reply)
    assert envelope == {"type": "document.filed", "data": {"paperless_id": 5}}


async def test_reply_to_other_users_message_is_ignored(family):
    """A reply to a human's message (not the bot's) yields no envelope —
    the ownership check guards reprocess from firing on chatter."""
    bart, homer = family["bart"], family["homer"]
    bot = _bot_over(bart)

    room_id = await _room_with_members(bart, [homer])
    await _await_room(bart, room_id, members=2)

    # Homer posts; Homer replies to himself. Nothing of the bot's here.
    await homer.room_send(
        room_id, "m.room.message", {"msgtype": "m.text", "body": "anchor from homer"},
    )
    anchor = await _find_event(bart, room_id, "anchor from homer")
    assert anchor is not None
    await homer.room_send(
        room_id, "m.room.message",
        {
            "msgtype": "m.text",
            "body": "replying to myself",
            "m.relates_to": {"m.in_reply_to": {"event_id": anchor.event_id}},
        },
    )
    reply = await _find_event(bart, room_id, "replying to myself")
    assert reply is not None
    assert await bot._reply_parent_envelope(room_id, reply) is None
