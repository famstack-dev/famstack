"""room_people: who is in the room a message came from, for the memory's reads.

What the agent may read depends on who will see the answer: a person's own
notes only in that person's own chat (`stack.cards.audience`). So each
message carries the people in its room, and each read of its turn names
them to the host API as its audience, signed with the agent's audience key
(`stack.agent_audience`).

    message in      metadata={"event_id": "$1"}
    with_people     metadata={"event_id": "$1", "room_people": ["bart", "marge"]}
    a tool call     turn.current().people == ("bart", "marge")
    its command     STACK_AUDIENCE=bart,marge:<expiry>:<mac>  ->  stack shim  ->  host API

Where it fits: `sitecustomize` wraps the Matrix channel's `_handle_message`
with `with_people`, reading the members nio keeps for the room
(`client.rooms[room_id].users`). nanobot copies a message's metadata into
the request context its tools run in (nanobot 0.3.5 `agent/loop.py`,
`_request_context_for_turn`), so the people stay with the turn they belong
to, also when two rooms are answered at once. `turn.current()` reads them
back, and `audience_env` signs them onto a command: a tool's, and exec's
through the environment nanobot builds for it.

A person is a member who is not one of the stack's own accounts. The
agent's own account, a bot (a localpart ending in `-bot`) and the tech
admin, which joins every room, are no audience. That is the bot runtime's
rule (`MicroBot.is_bot_user`), repeated here because this container does
not carry the framework.

SAFETY: a room the client does not know gets no people, and no people
means no audience: the API then refuses a memory read rather than answer
from the admin's view. More members than there are listening only narrows
what is read, so an invited member counts as listening. The key stays in
this process: `audience_env` takes it out of every command's environment
and puts only the signature in, so a command cannot sign people itself.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable, Mapping
from typing import Any

from agent_audience import AUDIENCE_KEY, listeners, sign

__all__ = ["METADATA_KEY", "AUDIENCE_ENV", "people_in", "with_people", "audience_env"]

# Where a message's people are kept in its metadata.
METADATA_KEY = "room_people"

# The variable a command reads its audience from (the agent's `stack` shim).
AUDIENCE_ENV = "STACK_AUDIENCE"

# The tech admin's account (`stack.users.TECH_ADMIN_USERNAME`).
TECH_ADMIN = "stackadmin"


def people_in(member_ids: Iterable[str], agent_id: str) -> list[str]:
    """The person slugs among a room's members, sorted.

        people_in(["@marge:home", "@archivist-bot:home", "@stacky:home"], "@stacky:home")
        # ["marge"]
    """
    people = set()
    for member in member_ids:
        local = member.split(":")[0].lstrip("@")
        if member == agent_id or local.endswith("-bot") or local == TECH_ADMIN or not local:
            continue
        people.add(local.lower())
    return sorted(people)


def with_people(metadata: Mapping[str, Any] | None, client: Any, room_id: str,
                agent_id: str) -> dict[str, Any]:
    """A message's metadata with the people in its room; unchanged (copied)
    when the client does not know the room."""
    metadata = dict(metadata or {})
    room = (getattr(client, "rooms", None) or {}).get(room_id)
    if room is not None:
        metadata[METADATA_KEY] = people_in(getattr(room, "users", None) or {}, agent_id)
    return metadata


def audience_env(env: dict[str, str], people: Iterable[str]) -> dict[str, str]:
    """`env` with `STACK_AUDIENCE` holding `people`, signed with the runtime's
    key; without it when nobody is known or the runtime has no key.

    A value from the container's own environment never stands in, and the
    key itself is never in what comes back.
    """
    env = {name: value for name, value in env.items() if name not in (AUDIENCE_ENV, AUDIENCE_KEY)}
    key = os.environ.get(AUDIENCE_KEY, "")
    people = list(people)
    if key and listeners(people):
        env[AUDIENCE_ENV] = sign(people, key, now=time.time())
    return env
