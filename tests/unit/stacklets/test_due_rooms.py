"""DueRooms: which rooms a bot reads the history of after a sync.

One test per rule of `due_rooms.py`. Rule 2 (anchor a room without a
cursor), the dispatch half of rule 5 and the reading of `M_FORBIDDEN` as a
left room belong to `MicroBot`; `test_microbot.py::TestReadAfterSync`
covers them.

The sync responses here are parsed by nio from the JSON Synapse sends, so
`active_rooms` is read against the real shape, not a stand-in.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from nio import SyncResponse

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))

from due_rooms import DueRooms, active_rooms  # noqa: E402

A, B, C = "!a:server", "!b:server", "!c:server"


def _sync(join=None, leave=None):
    return SyncResponse.from_dict({
        "next_batch": "s1",
        "rooms": {"join": join or {}, "leave": leave or {}},
    })


def _timeline(*, events=0, limited=False):
    return {"timeline": {
        "events": [{
            "type": "m.room.message", "event_id": f"$e{i}",
            "sender": "@homer:server", "origin_server_ts": i,
            "content": {"msgtype": "m.text", "body": "hi"},
        } for i in range(events)],
        "limited": limited,
    }}


_RECEIPT = {"ephemeral": {"events": [{
    "type": "m.receipt",
    "content": {"$e7": {"m.read": {"@other-bot:server": {"ts": 7}}}},
}]}}
_TYPING = {"ephemeral": {"events": [{
    "type": "m.typing", "content": {"user_ids": ["@homer:server"]},
}]}}
_ACCOUNT_DATA = {"account_data": {"events": [{
    "type": "m.fully_read", "content": {"event_id": "$e7"},
}]}}


def _settled(*joined) -> DueRooms:
    """A DueRooms for `joined` after the read at start finished everywhere."""
    due = DueRooms(joined)
    for room_id in joined:
        due.read_finished(room_id)
    return due


# ── Rule 1: at start ───────────────────────────────────────────────────────

def test_every_joined_room_is_due_at_start():
    """The sync token survives a restart, so only a read of every room finds
    a message whose handler the stop cut off."""
    assert DueRooms([B, A]).due() == {A, B}


# ── Rule 3: after a sync ───────────────────────────────────────────────────

def test_a_sync_makes_due_only_the_rooms_it_reports_as_active():
    due = _settled(A, B, C)

    due.after_sync({B})

    assert due.due() == {B}


def test_an_idle_sync_makes_no_room_due():
    due = _settled(A, B)

    due.after_sync(active_rooms(_sync()))

    assert due.due() == set()


class TestActiveRooms:
    """What in a sync response counts as activity."""

    def test_a_room_with_timeline_events_is_active(self):
        assert active_rooms(_sync({A: _timeline(events=2), B: _timeline()})) == {A}

    def test_a_room_with_a_gap_is_active(self):
        """`limited` means the server left out events since the last sync:
        they are on the timeline, not in the response."""
        assert active_rooms(_sync({A: _timeline(limited=True)})) == {A}

    def test_a_room_the_sync_reports_as_left_is_active_once(self):
        """A leave's timeline ends with the leave. The server still answers
        a member who left with the history up to the leave, so a message
        sent just before the removal is read once more."""
        assert active_rooms(_sync(leave={A: _timeline(events=2)})) == {A}

    @pytest.mark.parametrize("activity", [_RECEIPT, _TYPING, _ACCOUNT_DATA],
                             ids=["receipt", "typing", "account-data"])
    def test_receipts_typing_and_account_data_are_not_activity(self, activity):
        """Every bot moves its read receipt after each message it handles,
        and the receipt reaches every other bot in the room. Counting it
        would bring back a read per bot after every handled message."""
        assert active_rooms(_sync({A: activity})) == set()


# ── Rule 4: after a sync that raised ───────────────────────────────────────

def test_a_failed_sync_makes_every_joined_room_due():
    """nio stores the new sync token before it runs the event callbacks, so
    a sync that raised may have consumed activity the bot never saw."""
    due = _settled(A, B)

    due.after_sync_error([A, B])

    assert due.due() == {A, B}


# ── Rule 5: a read that failed ─────────────────────────────────────────────

def test_a_room_whose_read_failed_stays_due_through_idle_syncs():
    due = _settled(A)
    due.after_sync({A})

    assert due.read_failed(A, now=0.0) is True
    due.after_sync(set())

    assert due.due() == {A}


def test_reads_that_fail_within_five_minutes_keep_trying():
    """Passes can come seconds apart, so their number says nothing about
    how long the server has been failing."""
    due = _settled(A)
    due.after_sync({A})

    tries = [due.read_failed(A, now=t) for t in (0.0, 0.1, 0.2, 10.0, 299.0)]

    assert tries == [True] * 5


def test_a_read_gives_up_once_reads_have_failed_for_five_minutes():
    """A page that fails every time would otherwise keep the room unread
    for good, its new messages included."""
    due = _settled(A)
    due.after_sync({A})
    due.read_failed(A, now=1_000.0)

    assert due.read_failed(A, now=1_300.0) is False
    assert due.read_failed(A, now=1_301.0) is False     # until a read finishes
    assert due.due() == {A}


def test_a_read_that_got_every_page_resets_the_clock_and_keeps_the_room_due():
    """Handling what the read got can still fail; the room stays due for
    that, but its pages came back, so the server is not failing."""
    due = _settled(A)
    due.read_failed(A, now=0.0)

    due.read_succeeded(A)

    assert due.read_failed(A, now=400.0) is True
    assert due.due() == {A}


def test_a_failed_sync_restarts_the_clocks_of_the_failing_rooms():
    """No read is tried while syncs fail, so an outage is no evidence that
    a room's page keeps failing."""
    due = _settled(A)
    due.read_failed(A, now=0.0)

    due.sync_failed()

    assert due.read_failed(A, now=400.0) is True
    assert due.read_failed(A, now=699.0) is True
    assert due.read_failed(A, now=700.0) is False
    assert due.due() == {A}


def test_a_read_that_succeeds_resets_the_clock():
    due = _settled(A)
    due.read_failed(A, now=0.0)

    due.read_finished(A)
    due.after_sync({A})

    assert due.read_failed(A, now=400.0) is True
    assert due.due() == {A}


# ── Rule 6: leaving the set ────────────────────────────────────────────────

def test_a_finished_read_takes_the_room_out_of_the_set():
    due = DueRooms([A, B])

    due.read_finished(A)

    assert due.due() == {B}


def test_a_room_whose_leave_was_read_stays_out_through_idle_syncs():
    due = _settled(A, B)
    due.after_sync(active_rooms(_sync(leave={A: _timeline(events=1)})))
    due.read_finished(A)

    due.after_sync(active_rooms(_sync()))

    assert due.due() == set()


def test_a_room_whose_read_was_forbidden_leaves_the_set():
    due = _settled(A)
    due.after_sync({A})

    due.left(A)

    assert due.due() == set()


def test_a_failed_sync_makes_a_room_the_bot_left_due_again():
    """The sync that raised may have carried a re-join and the first
    message after it. No later sync reports them again, so the room must be
    read; for a room the bot is still out of, that read is one request."""
    due = _settled(A, B)
    due.left(A)

    due.after_sync_error([A, B])

    assert due.due() == {A, B}


def test_a_room_the_bot_joins_again_is_read_again():
    due = _settled(A)
    due.left(A)

    due.after_sync({A})
    assert due.due() == {A}
    due.read_finished(A)
    due.after_sync_error([A])

    assert due.due() == {A}
