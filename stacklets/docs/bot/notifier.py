"""Notifier — the mid-flow status port for pipelines and services.

A collaborator that wants to post an ephemeral progress message
("fetching …", "looking deeper …") shouldn't know about Matrix or the
message catalogue. It takes a `Notifier` and names a translation key;
the orchestrator supplies a `MatrixNotifier` bound to the room + reply
thread, which translates and sends.

This generalises the ad-hoc `announce` callback that SearchService used
first — now that capture needs the same thing, it's a named port.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional, Protocol


class Notifier(Protocol):
    """Posts a translated, ephemeral status message into the conversation."""

    async def status(self, key: str, **kwargs) -> None: ...


class MatrixNotifier:
    """A Notifier bound to one room + reply thread, backed by the bot's
    formatted send and translator."""

    def __init__(
        self, *,
        room_id: str,
        reply_to: Optional[str],
        send: Callable[..., Awaitable[None]],
        t: Callable[..., str],
    ):
        self._room_id = room_id
        self._reply_to = reply_to
        self._send = send
        self._t = t

    async def status(self, key: str, **kwargs) -> None:
        await self._send(self._room_id, self._t(key, **kwargs), self._reply_to)
