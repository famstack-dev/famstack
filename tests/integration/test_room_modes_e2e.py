"""Per-room process modes + 🔖 bookmark reactions — end-to-end INTENT.

This file is an executable specification of how the archivist should
behave around per-room config and user-driven reactions. It is the
contract; the implementation is expected to satisfy it.

> STATUS: verified green through `stacktests` on 2026-08-02. It ran red
> first, and reconciling it took one fix on each side. The test was
> treating the bot's join as readiness, but a join is an
> `m.room.member` state event, so a sender-only wait clears the instant
> the invite is accepted rather than when the bot starts listening. The
> implementation anchored a room's message cursor at the first drain
> that noticed the room, which silently swallowed anything sent between
> joining and that drain — including the first thing a member types in
> reply to the welcome. Both are fixed; see `_anchor_cursor_on_join`.

The intent, in one place:

  Rooms have a `process` mode, set in chat with `!config`:

    `!config`                 -> show the room's current config + options
    `!config process auto`    -> file everything as it arrives (default)
    `!config process react`   -> ignore plain messages; act only on a
                                 user's reaction

  In a `react` room the bot stays quiet until a family member reacts 🔖
  (or 📌) to a message, which bookmarks it (the same capture `auto` mode
  would have made). In an `auto` room the bot processes messages as they
  land. Either way, a processed message ends up marked 👀 (picked up) and
  then ✅ (filed) or ❌ (failed) so the outcome is visible at a glance in
  the timeline, while the detailed reply lives in a thread.

  Room mode is stored in the bot's own room account data, so it works
  without granting the bot any room power level (see
  project_bot_power_level_rule).

Why a group room: a 2-member room with the bot is a DM, which always
reacts regardless of mode. We add Marge so the room has 3 members and
the mode gate actually applies.
"""
from __future__ import annotations

import time

from nio import AsyncClient

from tests.integration.matrix import (
    mxid,
    event_type,
    fetch_room_events,
    wait_for_room_event,
    wait_for_room_events_until,
)

ARCHIVIST = mxid("archivist-bot")
MARGE = mxid("marge")
EYES, CHECK = "👀", "✅"

def _norm(key: str) -> str:
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


def _bot_said(events, needle: str) -> bool:
    """Did the archivist post a message containing `needle`?"""
    return any(
        getattr(e, "sender", None) == ARCHIVIST
        and needle.lower() in (getattr(e, "body", "") or "").lower()
        for e in events
    )


async def _send(client, room_id: str, body: str) -> str:
    r = await client.room_send(
        room_id, "m.room.message", {"msgtype": "m.text", "body": body},
    )
    return r.event_id


async def _react(client, room_id: str, target: str, key: str) -> None:
    await client.room_send(room_id, "m.reaction", {"m.relates_to": {
        "rel_type": "m.annotation", "event_id": target, "key": key}})


async def test_room_modes_and_bookmark_reactions(homer, matrix):
    """The full contract, driven as Homer in a 3-member group room.

    1. `!config process react` is acknowledged and persists.
    2. In react mode a bare URL is NOT processed (no 👀, no filing).
    3. 🔖 on that message bookmarks it: the bot marks it 👀 then ✅.
    4. `!config process auto` is acknowledged.
    5. In auto mode a bare URL is processed without any reaction.
    """
    marge_creds = matrix["marge"]
    marge = AsyncClient(marge_creds.homeserver, marge_creds.user_id)
    marge.access_token = marge_creds.access_token
    marge.device_id = marge_creds.device_id
    try:
        # 3-member group room: Homer + Marge + the archivist.
        created = await homer.room_create(
            name=f"modes-e2e-{int(time.time())}", invite=[MARGE, ARCHIVIST],
        )
        room = created.room_id
        await marge.join(room)

        # The bot auto-accepts the invite and posts a welcome on join.
        # Wait for the welcome *message*, not merely for the archivist to
        # appear in the timeline: a join is an `m.room.member` state event
        # with the bot as sender, so a sender-only predicate is satisfied
        # the instant it accepts the invite, before it is processing
        # anything. A command sent in that window is swallowed as sync
        # history and answered by nothing. Posting the welcome is the
        # bot's own "I am listening" signal, so that is the gate.
        # (Cold start can take ~40s.)
        joined = await wait_for_room_event(
            homer,
            room,
            lambda e: (
                getattr(e, "sender", None) == ARCHIVIST
                and (getattr(e, "body", "") or "").strip() != ""
            ),
            timeout=130,
        )
        assert joined, \
            "archivist never posted its welcome, so it is not listening yet"

        # 1. Switch the room to react mode.
        await _send(homer, room, "!config process react")
        ack_event = await wait_for_room_event(
            homer,
            room,
            lambda e: _bot_said([e], "is now") and _bot_said([e], "react"),
            timeout=30,
        )
        ack = [ack_event] if ack_event else []
        assert _bot_said(ack, "is now") and _bot_said(ack, "react"), \
            "expected an acknowledgement that the room is now in react mode"

        # `!config` (bare) shows the current config + options.
        await _send(homer, room, "!config")
        status_event = await wait_for_room_event(
            homer,
            room,
            lambda e: _bot_said([e], "room config") and _bot_said([e], "process"),
            timeout=30,
        )
        status = [status_event] if status_event else []
        assert _bot_said(status, "room config") and _bot_said(status, "process"), \
            "bare !config should print the current config and its options"

        # 2. React mode gates auto-processing: a bare URL is left alone.
        e1 = await _send(homer, room, "https://en.wikipedia.org/wiki/Camping")
        gated = await fetch_room_events(homer, room, duration=40)
        assert not _bot_reacted(gated, key=EYES, target=e1), \
            "react mode must not auto-process a plain message"
        assert not _bot_said(gated, "captured"), \
            "react mode must not file a plain message"

        # 3. 🔖 triggers the capture: 👀 then ✅ on the bookmarked message.
        await _react(homer, room, e1, "🔖")
        done = await wait_for_room_events_until(
            homer,
            room,
            lambda events: _bot_reacted(events, key=CHECK, target=e1),
            timeout=120,
        )
        assert _bot_reacted(done, key=EYES, target=e1), \
            "🔖 should make the bot pick up the message (👀)"
        assert _bot_reacted(done, key=CHECK, target=e1), \
            "a successful bookmark should be marked ✅"

        # 4. Switch back to auto mode.
        await _send(homer, room, "!config process auto")
        ack2_event = await wait_for_room_event(
            homer,
            room,
            lambda e: _bot_said([e], "is now") and _bot_said([e], "auto"),
            timeout=30,
        )
        ack2 = [ack2_event] if ack2_event else []
        assert _bot_said(ack2, "is now") and _bot_said(ack2, "auto"), \
            "expected an acknowledgement that the room is now in auto mode"

        # 5. Auto mode processes a bare URL with no reaction needed.
        e2 = await _send(homer, room, "https://en.wikipedia.org/wiki/Hiking")
        auto = await wait_for_room_events_until(
            homer,
            room,
            lambda events: _bot_reacted(events, key=CHECK, target=e2),
            timeout=120,
        )
        assert _bot_reacted(auto, key=EYES, target=e2), \
            "auto mode should pick up a plain message (👀)"
        assert _bot_reacted(auto, key=CHECK, target=e2), \
            "auto mode should file it and mark ✅"
    finally:
        await marge.close()
