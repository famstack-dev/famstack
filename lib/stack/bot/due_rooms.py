"""Which rooms a bot must read the history of after a sync.

A bot handles messages by reading each room's history back to a per-room
cursor (`MicroBot._drain`). Reading every room after every sync is safe
but costly: an idle stack syncs every 30 s per bot, so each pass sends a
history request per bot and room, and an event the bot cannot decrypt
fails again on every pass. Reading too few rooms loses messages, which is
worse. This module holds the rules that decide between the two.

A room is *due* when it can hold events past its cursor that the bot has
not handled:

1. At start, every joined room is due. The sync token survives a restart,
   so the first sync reports only what happened since, not a message whose
   handler the stop cut off.
3. After a sync, a room with timeline events, or with a gap (`limited`),
   becomes due. Receipts, typing and account data do not count: every
   bot's read receipt reaches every other bot in the room, so counting
   them would bring back a read per bot after every handled message. A
   room the sync reports as left counts the same way: its timeline ends
   with the leave, and the server still returns the history up to the
   leave, so a message sent just before it is read once more.
4. After a sync that raised, every room the client knows becomes due, the
   ones the bot left included. nio stores the new sync token before it runs
   the event callbacks, so a callback that raises makes `sync()` raise
   after the activity is already consumed, and that activity can be a
   re-join to a room the bot had left. (`MicroBot` also makes the pass
   after a failed sync wait and read nothing.)
5. A read that failed leaves the room due, so the next pass reads it again
   from the same cursor. A page that fails every time would keep the room
   unread for good, its new messages included, so once a room's reads
   have failed for `GIVE_UP_AFTER_SECONDS` in a row, a failing read
   handles what it could read. The room stays due until a read reaches
   its cursor. Time, not passes: passes can come seconds apart. A failed
   sync restarts the clocks, because no read is tried while syncs fail.
6. A room stops being due after a read that reached its cursor (a leave's
   read included), when the server refuses the read (`M_FORBIDDEN`), or
   when nio never joined it. It is due again on its next timeline event
   or failed sync.

Rule 2 (a joined room without a cursor is anchored on its first read, a
little before now, so its first message is read), the initial sync that rule
1 needs, the wait after a failed sync, and the rest of
rule 5 (a failed read dispatches nothing, until the reads have failed for
long enough) change the cursor or do I/O, so they live in `MicroBot`.
This module is pure: room ids in, room ids out, no client, and the time
passed in rather than read from a clock.

    due = DueRooms(client.rooms)          # at start
    due.after_sync(active_rooms(resp))    # after each sync
    due.after_sync_error(client.rooms)    # after a sync that raised
    due.sync_failed()                     # after a sync that raised or failed
    for room_id in due.due():
        ...read it: due.read_succeeded(room_id) when it got every page,
        then due.read_finished(room_id) once all of it was handled;
        or, when a page failed, if not due.read_failed(room_id, now):
        handle what was read
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = ["GIVE_UP_AFTER_SECONDS", "DueRooms", "active_rooms"]

# How long a room's reads fail in a row before a read gives up on the
# pages it could not get: long enough for a server restart, short enough
# that new messages are not held up for long.
GIVE_UP_AFTER_SECONDS = 300.0


class DueRooms:
    """The set of rooms whose history the next pass must read.

    `MicroBot` tells it what happened (a sync, a failed sync, a read that
    finished or failed, a room left) and asks `due()` which rooms to read.
    """

    def __init__(self, joined: Iterable[str]) -> None:
        """Rule 1: every room joined at start is due."""
        self._due: set[str] = set(joined)
        # When each room's current run of failed reads began, in the
        # caller's clock (seconds).
        self._failing_since: dict[str, float] = {}

    def after_sync(self, active: Iterable[str]) -> None:
        """Rule 3: rooms with new timeline events or a gap become due."""
        self._due.update(active)

    def after_sync_error(self, rooms: Iterable[str]) -> None:
        """Rule 4: every room the client knows becomes due.

        `rooms` includes the rooms the bot left, because nio keeps them. The
        server answers a read of one the bot is still out of with
        `M_FORBIDDEN`, or with its history up to the leave, so the read
        can still dispatch a message from before the leave that was never
        handled.
        """
        self._due.update(rooms)

    def sync_failed(self) -> None:
        """Rule 5: restart the clocks of the rooms whose reads are failing.

        No read is tried while syncs fail, so the time of an outage says
        nothing about whether a room's page keeps failing. Without this,
        one failing read after an outage longer than
        `GIVE_UP_AFTER_SECONDS` would give up at once.
        """
        self._failing_since.clear()

    def read_succeeded(self, room_id: str) -> None:
        """Rule 5: a read got every page; its room's failure clock starts again.

        The room stays due until `read_finished`: handling what the read
        got can still fail, and then the next pass handles it again.
        """
        self._failing_since.pop(room_id, None)

    def read_finished(self, room_id: str) -> None:
        """Rule 6: a read reached the cursor and everything it got was handled."""
        self._due.discard(room_id)
        self._failing_since.pop(room_id, None)

    def read_failed(self, room_id: str, now: float) -> bool:
        """Rule 5: note a failed read at `now`; True while the room should be read again.

        `now` is a monotonic clock in seconds, passed in so this module has
        none. The room stays due either way. False once the room's reads
        have failed for `GIVE_UP_AFTER_SECONDS` in a row: the caller then
        handles what it read. `read_succeeded`, `read_finished`, `left`
        and `sync_failed` start the clock again.
        """
        self._due.add(room_id)
        since = self._failing_since.setdefault(room_id, now)
        return now - since < GIVE_UP_AFTER_SECONDS

    def left(self, room_id: str) -> None:
        """Rule 6: the bot is not in the room.

        The server refused the read (`M_FORBIDDEN`, as for a banned
        member), or nio never joined the room. Idle syncs do not read it
        again.
        """
        self._due.discard(room_id)
        self._failing_since.pop(room_id, None)

    def due(self) -> set[str]:
        """The rooms to read on this pass."""
        return set(self._due)


def active_rooms(sync) -> set[str]:
    """The rooms of a sync response that can hold new events.

    `sync` is a nio `SyncResponse`. A joined room, or one the sync reports
    as left, counts when its timeline has events or is `limited` (the
    server left out events between the last sync and this one).
    """
    return {
        room_id
        for section in (sync.rooms.join, sync.rooms.leave)
        for room_id, info in section.items()
        if info.timeline.events or info.timeline.limited
    }
