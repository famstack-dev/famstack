"""The hub: one place that keeps the latest events and answers "what came after this?"

A screen, the poll fallback, a bot and a test all read it the same way:
`after(cursor, timeout=...)` returns what came after the cursor, waits up
to `timeout` when nothing has, and returns `[resync]` for a cursor it
cannot continue from, so the reader reloads its view instead of showing
a gap. The SSE route sends each event's cursor as its id, and a browser
sends the last one back when it reconnects; these tests read it as such
a reader does.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "lib"))

from stack.live.hub import Hub  # noqa: E402


def step(hub: Hub, text: str):
    return hub.publish("step.done", {"text": text}, subject="job/7")


def texts(events) -> list[str]:
    return [e.data.get("text") for e in events]


# ── Reading after a cursor ───────────────────────────────────────────────

def test_a_reader_gets_exactly_what_came_after_the_event_it_saw():
    hub = Hub()
    seen = step(hub, "Rendering environment")
    step(hub, "Pulling images")
    step(hub, "Starting containers")

    assert texts(hub.after(seen.id, timeout=0)) == ["Pulling images", "Starting containers"]


def test_each_event_carries_its_cursor_and_what_it_is_about():
    hub = Hub()
    event = step(hub, "Rendering environment")

    assert event.id == hub.cursor
    assert event.subject == "job/7"
    assert (event.type, event.data) == ("step.done", {"text": "Rendering environment"})


def test_without_a_cursor_a_reader_gets_only_what_comes_next():
    hub = Hub()
    step(hub, "before the reader")
    later = threading.Timer(0.05, step, args=(hub, "after the reader"))
    later.start()

    assert texts(hub.after(None, timeout=5)) == ["after the reader"]


def test_a_waiting_reader_hears_of_an_event_at_once():
    """It waits on the hub, not on a clock: the event, not the timeout,
    ends the wait."""
    hub = Hub()
    threading.Timer(0.05, step, args=(hub, "Pulling images")).start()

    began = time.monotonic()
    got = hub.after(hub.cursor, timeout=5)

    assert texts(got) == ["Pulling images"]
    assert time.monotonic() - began < 1


def test_nothing_new_within_the_timeout_is_an_empty_answer():
    """The SSE route sends a heartbeat for it; the poll route answers it."""
    hub = Hub()
    step(hub, "Rendering environment")

    began = time.monotonic()
    assert hub.after(hub.cursor, timeout=0.1) == []
    assert time.monotonic() - began >= 0.1


# ── A cursor the hub cannot continue from ───────────────────────────────

def test_a_cursor_from_before_a_restart_gets_a_resync():
    """A new tools server is a new hub, and what the old one sent is gone."""
    before = Hub()
    seen = step(before, "Pulling images")
    after_restart = Hub()
    step(after_restart, "Rendering environment")

    got = after_restart.after(seen.id, timeout=0)

    assert [e.type for e in got] == ["resync"]
    assert got[0].id == after_restart.cursor


def test_a_cursor_that_fell_out_of_the_kept_window_gets_a_resync():
    hub = Hub(keep=3)
    seen = step(hub, "1")
    for text in ("2", "3", "4", "5"):
        step(hub, text)

    assert [e.type for e in hub.after(seen.id, timeout=0)] == ["resync"]


def test_the_oldest_cursor_still_kept_is_continued():
    hub = Hub(keep=3)
    for text in ("1", "2", "3", "4", "5"):
        last = step(hub, text)
        if text == "2":
            seen = last

    assert texts(hub.after(seen.id, timeout=0)) == ["3", "4", "5"]


def test_a_malformed_cursor_gets_a_resync():
    hub = Hub()
    step(hub, "Rendering environment")
    future = f"{hub.epoch}.99"

    for cursor in ("", "garbage", f"{hub.epoch}.x", f"{hub.epoch}", future):
        assert [e.type for e in hub.after(cursor, timeout=0)] == ["resync"], cursor


def test_a_resync_carries_the_cursor_to_go_on_from():
    hub = Hub()
    step(hub, "1")

    resync = hub.after("garbage", timeout=0)[0]
    step(hub, "2")

    assert texts(hub.after(resync.id, timeout=0)) == ["2"]


# ── Several threads ──────────────────────────────────────────────────────

def test_readers_in_several_threads_each_see_every_event_once_in_order():
    """Runs publish from their own threads and the routes read from a
    thread pool: no reader loses or repeats an event, and none waits on
    another."""
    hub = Hub(keep=1000)
    start = hub.cursor
    publishers, each = 4, 50
    seen: list[list[str]] = [[], [], []]

    def read(into: list[str]) -> None:
        cursor = start
        while len(into) < publishers * each:
            for event in hub.after(cursor, timeout=2):
                into.append(event.id)
                cursor = event.id

    def publish(n: int) -> None:
        for i in range(each):
            step(hub, f"{n}-{i}")

    readers = [threading.Thread(target=read, args=(s,)) for s in seen]
    writers = [threading.Thread(target=publish, args=(n,)) for n in range(publishers)]
    for t in readers + writers:
        t.start()
    for t in readers + writers:
        t.join(timeout=10)

    in_order = [e.id for e in hub.after(start, timeout=0)]
    assert len(set(in_order)) == publishers * each
    assert seen == [in_order] * len(seen)
