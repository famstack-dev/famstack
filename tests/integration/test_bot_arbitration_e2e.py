"""Which bot acts on a message, and which stays quiet — end-to-end INTENT.

This file is an executable specification of `docs/design/brain/who-answers.md`.
It is the contract; the implementation is expected to satisfy it.

> STATUS: written 2026-08-06 alongside the fix, NOT yet reconciled against
> the rig. Marked `unverified` until a green run confirms it, per the
> marker's contract in pyproject.toml.

A famstack room holds several bots and several people, and the invariant
is that exactly one component answers a message. Two rules decide it, and
both were learned the hard way:

  **Chat is not material.** In a room with more than one person in it,
  the archivist stops guessing from message shape. A long message there
  is usually just somebody talking, and filing those produced notes named
  after an agent's error message. Keeping something is 📌, which is
  explicit, visible in the timeline, and works at any length under any
  room mode. Alone in a room there is nobody to talk to, so a long paste
  is still material and is still filed.

  **A thread has an owner.** It belongs to the first bot that replied
  into it, other than whoever started it. The archivist acts inside a
  thread only when it owns one. Without this, a family's conversation
  with the agent was filed as notes, and because the archivist's own card
  then sat in the thread, every following line read as a correction to
  it: one paste became a reclassification loop that could not be stopped
  by talking.

Why the two rooms below are shaped as they are:

  * the group room has two humans, which is what turns the ambient text
    path off. Reactions, links and files are unaffected, and the test
    checks that too — the point is that the *guessing* stopped, not that
    the archivist went deaf.
  * the solo room has one human, so ambient capture is live. That is what
    makes it the honest place to test the thread rule: a paste there is
    filed on the main timeline and ignored inside another bot's thread,
    so the only thing that can explain the difference is ownership.

stacker-bot stands in for the family agent. The archivist reads the
framework's `-bot` convention, not a specific bot's name, so any famstack
bot exercises the same branch, and stacker-bot is already in the rig
while the agent stacklet may not be.
"""
from __future__ import annotations

import time

import pytest
from nio import AsyncClient

from tests.integration.matrix import (
    event_type,
    fetch_room_events,
    mxid,
    wait_for_room_event,
    wait_for_room_events_until,
)

pytestmark = pytest.mark.unverified

ARCHIVIST = mxid("archivist-bot")
STACKER = mxid("stacker-bot")
MARGE = mxid("marge")
EYES, CHECK = "👀", "✅"

# Comfortably over `looks_like_paste`'s 100-character threshold, so the
# only reason the archivist could ignore it is the rule under test.
WALL_OF_TEXT = (
    "Right, the plan is to load the car at four, leave by five, and stop "
    "at the halfway services for dinner around seven so nobody has to "
    "cook when we arrive."
)


def _norm(key: str) -> str:
    """Drop the variation selector clients append to an emoji key."""
    return (key or "").replace("\uFE0F", "").strip()


def _bot_reacted(events, *, key: str, target: str) -> bool:
    """Did the archivist add reaction `key` to event `target`?"""
    return any(
        event_type(e) == "m.reaction"
        and getattr(e, "sender", None) == ARCHIVIST
        and getattr(e, "reacts_to", None) == target
        and _norm(getattr(e, "key", "")) == key
        for e in events
    )


def _bot_replied_to(events, target: str) -> bool:
    """Did the archivist post a message answering `target`?

    Anchored on the reply relation rather than "the archivist said
    something", because its join welcome is already in every room's
    timeline and would satisfy the looser check on its own.
    """
    for e in events:
        if getattr(e, "sender", None) != ARCHIVIST:
            continue
        relates = (
            (getattr(e, "source", None) or {})
            .get("content", {})
            .get("m.relates_to", {})
        )
        if relates.get("m.in_reply_to", {}).get("event_id") == target:
            return True
    return False


async def _send(client, room_id: str, body: str, **content) -> str:
    r = await client.room_send(
        room_id, "m.room.message",
        {"msgtype": "m.text", "body": body, **content},
    )
    return r.event_id


async def _send_in_thread(client, room_id: str, root: str, body: str) -> str:
    return await _send(client, room_id, body, **{"m.relates_to": {
        "rel_type": "m.thread", "event_id": root,
        "is_falling_back": True, "m.in_reply_to": {"event_id": root},
    }})


async def _react(client, room_id: str, target: str, key: str) -> None:
    await client.room_send(room_id, "m.reaction", {"m.relates_to": {
        "rel_type": "m.annotation", "event_id": target, "key": key}})


async def _wait_until_listening(client, room_id: str) -> None:
    """Wait for the archivist's welcome, not merely for it to join.

    A join is an `m.room.member` state event, so a sender-only predicate
    clears the instant the invite is accepted, before the bot is
    processing anything. Its welcome is its own "I am listening" signal.
    Cold start can take ~40s. (Same trap as test_room_modes_e2e.py.)
    """
    posted = await wait_for_room_event(
        client, room_id,
        lambda e: (
            getattr(e, "sender", None) == ARCHIVIST
            and (getattr(e, "body", "") or "").strip() != ""
        ),
        timeout=130,
    )
    assert posted, "archivist never posted its welcome, so it is not listening"


async def test_chat_between_people_is_not_filed_but_a_pin_is(homer, matrix):
    """Two humans in a room: the archivist stops reading intent from
    shape, and 📌 is how you override it.

    1. A wall of text is left alone. No pickup, no reply.
    2. 📌 on that same message files it: 👀 then ✅.
    3. A pasted link is still filed with no reaction needed, because
       pasting a URL is a deliberate act rather than a turn in a
       conversation.
    """
    marge_creds = matrix["marge"]
    marge = AsyncClient(marge_creds.homeserver, marge_creds.user_id)
    marge.access_token = marge_creds.access_token
    marge.device_id = marge_creds.device_id
    try:
        created = await homer.room_create(
            name=f"arbitration-group-{int(time.time())}",
            invite=[MARGE, ARCHIVIST],
        )
        room = created.room_id
        await marge.join(room)
        await _wait_until_listening(homer, room)

        # 1. Somebody talking is not material to file.
        chat = await _send(homer, room, WALL_OF_TEXT)
        quiet = await fetch_room_events(homer, room, duration=40)
        assert not _bot_reacted(quiet, key=EYES, target=chat), (
            "a long message in a room with other people in it is chat; "
            "the archivist must not pick it up"
        )

        # 2. The override. 📌 says "this one I do want kept", and it is
        #    the same capture the ambient path would have made.
        await _react(homer, room, chat, "📌")
        pinned = await wait_for_room_events_until(
            homer, room,
            lambda events: _bot_reacted(events, key=CHECK, target=chat),
            timeout=120,
        )
        assert _bot_reacted(pinned, key=EYES, target=chat), \
            "📌 should make the archivist pick the message up (👀)"
        assert _bot_reacted(pinned, key=CHECK, target=chat), \
            "a successful pin should be marked ✅"

        # 3. Only the guessing stopped. Links still file on their own.
        link = await _send(homer, room, "https://en.wikipedia.org/wiki/Camping")
        filed = await wait_for_room_events_until(
            homer, room,
            lambda events: _bot_reacted(events, key=CHECK, target=link),
            timeout=120,
        )
        assert _bot_reacted(filed, key=CHECK, target=link), (
            "a pasted link is a deliberate drop, not conversation, and "
            "must still be filed without a reaction"
        )
    finally:
        await marge.close()


async def test_the_archivist_stays_out_of_another_bots_thread(
    homer, matrix, test_stack,
):
    """One human in the room, so ambient capture is live. The same paste
    is filed on the main timeline and ignored inside a thread another bot
    owns, which leaves ownership as the only explanation.

    An @-mention still reaches the archivist in that thread: deliberate
    address beats ambient context, and it is the escape hatch that keeps
    the rule from locking the family out.
    """
    created = await homer.room_create(
        name=f"arbitration-solo-{int(time.time())}", invite=[ARCHIVIST],
    )
    room = created.room_id
    await _wait_until_listening(homer, room)

    # Baseline: alone in the room there is nobody to talk to, so a long
    # paste is material and the ambient path files it. Without this the
    # negative below would also pass on a bot that had simply stopped.
    solo = await _send(homer, room, WALL_OF_TEXT)
    filed = await wait_for_room_events_until(
        homer, room,
        lambda events: _bot_reacted(events, key=CHECK, target=solo),
        timeout=120,
    )
    assert _bot_reacted(filed, key=CHECK, target=solo), (
        "with one human in the room the ambient capture path must still "
        "file a paste, otherwise this test proves nothing below"
    )

    # Another bot opens a conversation: Homer asks, the bot answers in a
    # thread on his question. First reply, and not the thread's starter,
    # so the thread is the bot's.
    ask = await _send(homer, room, "what do we still need for camping?")
    sent = test_stack.run(
        "messages", "send", room,
        "The gas cartridge and the sleeping mats.", "--thread", ask,
    )
    assert sent.get("ok") is not False, f"stacker-bot send failed: {sent}"
    claimed = await wait_for_room_event(
        homer, room,
        lambda e: getattr(e, "sender", None) == STACKER,
        timeout=60,
    )
    assert claimed, "stacker-bot never posted, so no thread was claimed"

    # The same paste, now inside that conversation. It is Homer talking
    # to the other bot, and the archivist has no part in it.
    in_thread = await _send_in_thread(homer, room, ask, WALL_OF_TEXT)
    ignored = await fetch_room_events(homer, room, duration=40)
    assert not _bot_reacted(ignored, key=EYES, target=in_thread), (
        "the thread belongs to the bot that answered in it first; the "
        "archivist must not pick up a message there"
    )

    # The escape hatch: addressed on purpose, it answers anyway.
    asked = await _send_in_thread(
        homer, room, ask, f"{ARCHIVIST} what did we pack last summer",
    )
    answered = await wait_for_room_events_until(
        homer, room,
        lambda events: _bot_replied_to(events, asked),
        timeout=120,
    )
    assert _bot_replied_to(answered, asked), (
        "an @-mention is deliberate address and must reach the archivist "
        "even inside a thread it does not own"
    )
