"""Who a tool call is for: the person, the conversation and the turn.

A tool runs inside nanobot's handling of one message, and nanobot binds
that message's request context for exactly as long (a context variable,
so two rooms answered at the same time each see their own). This is the
one place the runtime reads it.

    now = current()
    now.person          # "homer", the vault slug of whoever sent the message
    now.conversation    # nanobot's session key: a room, or a thread in it
    now.id              # unique per turn
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    """The message a tool call belongs to; every field is "" outside a turn."""

    person: str = ""
    conversation: str = ""
    id: str = ""


def person_of(sender_id: str) -> str:
    """The vault slug of a Matrix sender: "@homer:simpson" is "homer"."""
    return str(sender_id or "").split(":")[0].lstrip("@")


def current() -> Turn:
    """The turn running now, or an empty one when nanobot has none."""
    from nanobot.agent.tools.context import current_request_context

    request = current_request_context()
    if request is None:
        return Turn()
    return Turn(
        person=person_of(getattr(request, "sender_id", "") or ""),
        conversation=str(getattr(request, "session_key", "") or getattr(request, "chat_id", "")
                         or ""),
        id=str(getattr(request, "turn_id", "") or ""),
    )
