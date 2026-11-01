"""RoomContext: what a bot knows about the room a message came from.

Every handler of a `MicroBot` asks the same questions before it acts on a
message: which room is this, is it a private chat with one person, does
the room have a name the bot routes by, and how did the people in it set
the bot up. A `RoomContext` answers them in one frozen value, built when
the message arrives and handed down to everything that handles it:

    ctx = await self._message_context(room)       # in a handler
    if not self._should_react(ctx, mentioned=mentioned):
        return                                     # the room said "react"
    language = ctx.config.get("language")          # an option of this bot

The room's identity (`room_id`, `alias`, `name`, `members`, `is_dm`)
comes from the nio room the bot already holds: attribute reads, no
network. The room's `config` is the bot's own settings for the room,
which people set with `!config <option> <value>` (`MicroBot.CONFIG_OPTIONS`
lists the options). It is stored on the homeserver as the bot's room
account data, `dev.famstack.room`, so reading it is a network call.

A worked case. Marge sets `!config process react` in "Family chat", then
Homer writes "milk is empty" there without naming the archivist:

    1. The handler builds the context:     one account-data read
         RoomContext(room_id="!family:simpson", alias="family-chat",
                     name="Family chat", members=(...4 people, bot),
                     is_dm=False, config={"process": "react"})
    2. Routing reads `ctx.config`:         "react", so the bot stays quiet
    3. In a room set to `auto` the message would be filed, and the
       filing reads its language from the same `ctx.config`: no second
       read

Why it works this way
---------------------

**Derived from the room on every message, never pinned at startup.** An
earlier design pinned one room id at the first sync. It missed a room
joined after that sync and broke when a room's alias changed, so each
message now gets a fresh snapshot.

**The config is read once per message and travels on the context.**
The routing gate used to read the account data, and a filing in the same
message read it again for its language: two network calls for one
message, and one more for every option a handler would read. One read
where the message enters gives routing and the handler the same value.

**Frozen.** A handler cannot change the context half-way through a
message, so what routing decided is what the rest of the code sees. A
later `!config` changes the next message's context, not this one's.

**A context without the config, for questions that need no setting.**
`context_for` without `config`, and `MicroBot._room_context`, build the
room's identity alone, with an empty `config`. Use them for questions that need no settings:
"is this the memories room?" before a message is routed, a welcome on
joining a room. A handler that routes or files by a setting takes the
context from `MicroBot._message_context`, the one way to get it with
its config.

What it does not do
-------------------

No flag such as "is the documents room". Which alias means what is a
bot's concept (the archivist's documents room), and the bot derives it
from `alias`. No cache of the config: a read is fresh for every message,
so a `!config` applies to the next one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

__all__ = ["RoomContext", "context_for", "normalize_alias"]


@dataclass(frozen=True)
class RoomContext:
    """The room a message came from, as the bot saw it when the message
    arrived, with the bot's settings for that room.

    ``room_id``   Addressing: passed to ``room_send``.
    ``alias``     Local part of the canonical alias, ``""`` when the room
                  has none (common for DMs). The routing key.
    ``name``      Display name. Useful in logs.
    ``members``   Sorted tuple of member user ids. Sorted so the value is
                  stable across syncs, although nio keeps ``room.users``
                  in a dict.
    ``is_dm``     True when the room has exactly two members and the bot
                  is one of them. Matrix's ``m.direct`` account data would
                  be more authoritative, but nio does not surface it, and
                  a two-person room with the bot behaves like a DM.
    ``config``    The bot's config for the room (``!config``), read-only:
                  option name to value, only the options someone set.
                  Empty when nobody set one, and on a context built
                  without it (``context_for`` without ``config``).

    Example, the context of a message in a room where Marge chose English
    filings:

        >>> ctx = RoomContext(room_id="!notes:simpson", alias="marge-notes",
        ...                   name="Marge notes",
        ...                   members=("@archivist-bot:simpson", "@marge:simpson"),
        ...                   is_dm=True, config={"language": "en"})
        >>> ctx.config.get("language"), ctx.config.get("process", "auto")
        ('en', 'auto')
    """

    room_id: str
    alias: str
    name: str
    members: tuple[str, ...]
    is_dm: bool
    # Not hashed: a mapping is not hashable, and the identity fields
    # already tell two rooms apart.
    config: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), hash=False,
    )


def context_for(
    room: Any, *, bot_user_id: str, config: Mapping[str, Any] | None = None,
) -> RoomContext:
    """Build the RoomContext of a nio ``MatrixRoom`` (or a duck type of it).

    Tolerant by design: a room can lack a canonical alias (DMs), a display
    name (fresh invites) or even a member list (briefly, during the first
    sync). Anything missing becomes an empty value; nothing here raises.

    ``bot_user_id`` is what DM detection needs. ``config`` is the room's
    config as the bot read it (``MicroBot.get_room_config``); it is copied
    into a read-only mapping, so a later change to the caller's dict does
    not reach the context. Without it the context has an empty config.
    Pure: no network, so a bot reads the config and passes it in
    (``MicroBot._message_context``).

        >>> from types import SimpleNamespace
        >>> room = SimpleNamespace(
        ...     room_id="!family:simpson", canonical_alias="#family-chat:simpson",
        ...     name="Family chat",
        ...     users={"@homer:simpson": 0, "@archivist-bot:simpson": 0})
        >>> ctx = context_for(room, bot_user_id="@archivist-bot:simpson",
        ...                   config={"process": "react"})
        >>> ctx.alias, ctx.is_dm, dict(ctx.config)
        ('family-chat', True, {'process': 'react'})
        >>> dict(context_for(room, bot_user_id="@archivist-bot:simpson").config)
        {}
    """
    room_id = getattr(room, "room_id", "") or ""
    alias = normalize_alias(getattr(room, "canonical_alias", None))
    name = getattr(room, "name", "") or ""
    members = tuple(sorted((getattr(room, "users", None) or {}).keys()))
    return RoomContext(
        room_id=room_id,
        alias=alias,
        name=name,
        members=members,
        is_dm=len(members) == 2 and bot_user_id in members,
        config=MappingProxyType(dict(config or {})),
    )


def normalize_alias(alias: str | None) -> str:
    """Strip a Matrix canonical alias down to its local part.

    Accepts either form a person or a homeserver supplies, so both ends of
    an alias comparison can go through it:

        >>> normalize_alias("#documents:home.local")
        'documents'
        >>> normalize_alias("documents")
        'documents'
        >>> normalize_alias(None)
        ''
    """
    if not alias:
        return ""
    a = alias.strip().lstrip("#")
    return a.split(":", 1)[0]
