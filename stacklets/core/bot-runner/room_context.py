"""RoomContext — per-event awareness of which Matrix room a message came from.

Part of the bot framework: every MicroBot subclass routes from this
snapshot rather than from any state pinned at startup. The previous
archivist-specific design pinned a single room id at first sync and
gated all routing on it; that broke in two ways:

  1. The first-sync hook is welcome-marker-gated by MicroBot, so a
     restart after the marker exists never re-runs the resolution —
     if the documents room hadn't been joined yet on first boot, it
     stayed invisible to routing forever.
  2. The pin was a one-shot snapshot. Renaming the room or moving the
     canonical alias after startup silently broke the docs path.

The framework now derives routing from the *current* room state on
every event. A `RoomContext` is a cheap value object built from the
nio room on each callback: a few attribute reads and a member-count
check, no network, no Matrix client state beyond what's already in
memory.

What handlers see
-----------------

  ``room_id``   Addressing — passed to ``room_send``.
  ``alias``     Local part of the canonical alias (``""`` if the room
                has none — common for DMs).
  ``name``      Display name. Useful in logs; the alias is the routing
                key.
  ``members``   Sorted tuple of member user-ids. Sorted so the value
                is stable across syncs even though nio backs
                ``room.users`` with a dict.
  ``is_dm``     True when the room has exactly two members and the bot
                is one of them. Matrix's ``m.direct`` account data
                would be more authoritative, but nio doesn't surface
                it cleanly and a 2-person room with the bot always
                behaves like a DM in practice.

The dataclass is frozen so handlers can't accidentally mutate it
half-way through a code path — every event gets a fresh snapshot.

Note: there's no "is the docs room" flag here. That's a bot-specific
concept (the archivist's documents-room alias) and the subclass owns
that derivation. The framework only knows about generic Matrix room
properties.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def normalize_alias(alias: str | None) -> str:
    """Strip a Matrix canonical alias down to its local part.

    Accepts either form a user or homeserver might supply:

      * ``"#documents:home.local"`` → ``"documents"``
      * ``"documents"``             → ``"documents"``
      * ``None`` / ``""``           → ``""``

    Both ends of any docs-room comparison go through this normalizer so
    the match is robust against either side carrying the full
    ``#name:server`` form.
    """
    if not alias:
        return ""
    a = alias.strip().lstrip("#")
    return a.split(":", 1)[0]


@dataclass(frozen=True)
class RoomContext:
    """Snapshot of a Matrix room at the moment an event arrived.

    Built once per event in the handler. Treat as read-only: it reflects
    state at construction time, and a later sync may move things on.
    """

    room_id: str
    alias: str
    name: str
    members: tuple[str, ...]
    is_dm: bool


def context_for(room: Any, *, bot_user_id: str) -> RoomContext:
    """Build a RoomContext from a nio MatrixRoom (or compatible duck type).

    Tolerant by design — Matrix rooms can lack a canonical alias (DMs),
    a display name (fresh invites), or even a populated member list
    (briefly during initial sync). Anything missing collapses to an
    empty / falsy value; nothing here raises.

    ``bot_user_id`` is the only configuration the framework needs to
    derive DM detection. Bot-specific routing flags (does this alias
    match my docs room? is this a notes room for entity X?) belong in
    the subclass on top of this generic snapshot.
    """
    room_id = getattr(room, "room_id", "") or ""
    raw_alias = getattr(room, "canonical_alias", None)
    alias = normalize_alias(raw_alias)
    name = getattr(room, "name", "") or ""

    users = getattr(room, "users", None) or {}
    members = tuple(sorted(users.keys()))

    is_dm = len(members) == 2 and bot_user_id in members

    return RoomContext(
        room_id=room_id,
        alias=alias,
        name=name,
        members=members,
        is_dm=is_dm,
    )
