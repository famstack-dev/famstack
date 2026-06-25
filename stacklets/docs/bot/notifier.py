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
    """Posts an ephemeral progress signal into the conversation — either
    a translated status line or a reaction on the source message."""

    async def status(self, key: str, **kwargs) -> None: ...

    async def acknowledge(self) -> None:
        """Signal 'picked this up, working on it' without a reply line."""
        ...


class MatrixNotifier:
    """A Notifier bound to one room + reply thread, backed by the bot's
    formatted send, translator, and (optionally) reaction transport."""

    def __init__(
        self, *,
        room_id: str,
        reply_to: Optional[str],
        send: Callable[..., Awaitable[None]],
        t: Callable[..., str],
        react: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ):
        self._room_id = room_id
        self._reply_to = reply_to
        self._send = send
        self._t = t
        self._react = react

    async def status(self, key: str, **kwargs) -> None:
        await self._send(self._room_id, self._t(key, **kwargs), self._reply_to)

    async def acknowledge(self) -> None:
        """React 👀 on the bound source message — the reaction-based
        replacement for the old "Reading …" status text. A no-op when no
        reaction transport or no source event is bound (e.g. a text-only
        flow), so callers never need to guard the call."""
        if self._react is not None and self._reply_to is not None:
            await self._react(self._room_id, self._reply_to)
