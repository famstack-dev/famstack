"""`stack memory diary` — what the compiler makes of a messy room.

The memories room is the hardest input in the stack: recordings arrive
days after they were made, one memo gets split across two files, a
caption trails its photo, and the only statement of when something
happened is a sentence spoken inside the audio. This file pins what the
compiler does about that.

Ground truth comes from `tools/family-memories/spec.en.yaml`, which was
authored to describe a real room's patterns and carries `true_date` and
`date_source` for every item. Asserting against that file rather than
against hand-written fixtures is deliberate: a fixture written next to
the compiler would only prove the two agree with each other.

The model's own accuracy is not tested here. `Reading`s are supplied
directly, so these tests pin what the compiler does *given* a reading --
whether the model produces a good one is a question for the rig, where
a real transcript meets a real model.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot"))
sys.path.insert(0, str(_REPO_ROOT / "tools" / "family-memories"))

import diary  # noqa: E402
from ingest import burst_ordered  # noqa: E402

SPEC = _REPO_ROOT / "tools" / "family-memories" / "spec.en.yaml"

# The room the corpus replays into. Burst members land back-to-back;
# everything else is spaced by the replay's own delay. Both are far
# tighter than a real room, which is exactly why the compiler takes the
# burst window as a parameter instead of assuming one.
SYNC_GAP_MS = 80
LIVE_GAP_MS = 2_100
WINDOW_S = 1.0
REPLAYED_ON = datetime(2026, 9, 12, 8, tzinfo=timezone.utc)
BASE_TS = int(REPLAYED_ON.timestamp() * 1000)


# ── Building a room ───────────────────────────────────────────────────


def _spec_items() -> list[dict]:
    return yaml.safe_load(SPEC.read_text())["items"]


def _room_from_spec(items: list[dict]) -> list[dict]:
    """The Matrix events the corpus produces, without a homeserver.

    Mirrors `ingest.py`: burst members contiguous, an edit sent as a
    separate `m.replace`, a caption as a reply. Timings follow the
    replay, not the items' true dates -- recovering those is the
    compiler's job, and handing them to it would test nothing.
    """
    events: list[dict] = []
    ids: dict[str, str] = {}
    ts = BASE_TS
    prev_burst = None

    for item in burst_ordered(items):
        burst = item.get("burst")
        ts += SYNC_GAP_MS if (burst and burst == prev_burst) else LIVE_GAP_MS
        prev_burst = burst

        event_id = ids[item["id"]] = f"${item['id']}"
        kind = item["kind"]
        if kind in ("voice", "dialogue"):
            content = {"msgtype": "m.audio", "body": f"{item['id']}.wav",
                       "url": f"mxc://test/{item['id']}",
                       "info": {"duration": 9000}}
        elif kind == "image":
            content = {"msgtype": "m.image", "body": f"{item['id']}.png",
                       "url": f"mxc://test/{item['id']}"}
        else:
            content = {"msgtype": "m.text", "body": item["text"].strip()}

        if target := item.get("reply_to"):
            content["m.relates_to"] = {"m.in_reply_to": {"event_id": ids[target]}}

        events.append({"type": "m.room.message", "event_id": event_id,
                       "sender": f"@{item['sender']}:test",
                       "origin_server_ts": ts, "content": content})

        if edit := item.get("edit_text"):
            ts += LIVE_GAP_MS
            events.append({
                "type": "m.room.message", "event_id": f"{event_id}#edit",
                "sender": f"@{item['sender']}:test", "origin_server_ts": ts,
                "content": {
                    "msgtype": "m.text", "body": "* " + edit.strip(),
                    "m.new_content": {"msgtype": "m.text", "body": edit.strip()},
                    "m.relates_to": {"rel_type": "m.replace",
                                     "event_id": event_id}},
            })
    return events


def _readings_from_spec(items: list[dict]) -> dict[str, diary.Reading]:
    """The reading a correct model would return for each item.

    `date_source: spoken` means the recording says its own date, so the
    reading carries it; anything else leaves it null and the compiler
    has to fall back. Fragment halves are marked where the spec says the
    recording was cut.
    """
    readings = {}
    for item in items:
        source = item["date_source"]
        fragment = item.get("fragment_of")
        first_half = fragment and item["id"].endswith("-a")
        second_half = fragment and not item["id"].endswith("-a")
        readings[f"${item['id']}"] = diary.Reading(
            mode="note" if item["kind"] == "text" else "monologue",
            spoken_date=(item["true_date"].isoformat()
                         if source == "spoken" else None),
            starts_mid_thought=bool(second_half),
            ends_mid_thought=bool(first_half),
        )
    return readings


def _words(item: dict) -> str:
    """What whisper would return: a memo's text, a dialogue's turns."""
    if turns := item.get("turns"):
        return " ".join(t["text"].strip() for t in turns)
    return item.get("text", "").strip()


def _transcribed(messages, items):
    """Stand in for whisper: a recording's body becomes its words.

    The compiler never sees a filename where a transcript belongs --
    `cli/diary.py` substitutes the decoded text before compiling, so the
    harness does too. Doing it here rather than in `_room_from_spec`
    keeps `resolve` under test against the events a room really holds.
    """
    spoken = {f"${i['id']}": _words(i) for i in items}
    return [m if m.kind != "voice" or not spoken.get(m.event_id)
            else replace(m, body=spoken[m.event_id])
            for m in messages]


def _compile(items=None):
    items = items if items is not None else _spec_items()
    messages = diary.resolve(_room_from_spec(items), burst_window_s=WINDOW_S)
    messages = _transcribed(messages, items)
    return diary.compile_entries(messages, _readings_from_spec(items))


def _entry_for(entries, item_id: str) -> diary.Entry:
    for entry in entries:
        if f"${item_id}" in entry.event_ids:
            return entry
    raise AssertionError(f"no entry carries {item_id}")


def _msg(event_id="$a", sender="marge", ts=BASE_TS, kind="voice", **kw):
    return diary.Message(event_id=event_id, sender=sender, ts=ts,
                         kind=kind, **kw)


# ── Matrix mechanics ──────────────────────────────────────────────────


class TestResolve:
    """What the room means before anyone reads it."""

    def test_an_edited_message_keeps_only_its_final_wording(self):
        events = _room_from_spec(_spec_items())
        messages = diary.resolve(events, burst_window_s=WINDOW_S)

        edited = next(m for m in messages if m.event_id == "$text-tagebuch")
        assert "Maggie's First March" in edited.body
        assert not any(m.event_id.endswith("#edit") for m in messages), \
            "an edit is a correction to a message, not a message"

    def test_a_redacted_message_leaves_no_trace(self):
        events = [{"type": "m.room.message", "event_id": "$gone",
                   "sender": "@marge:test", "origin_server_ts": BASE_TS,
                   "content": {}}]
        assert diary.resolve(events) == []

    def test_a_reply_does_not_quote_its_parent_back(self):
        body = "> <@marge:test> the original memo\n\nHe gets that from me."
        events = [{"type": "m.room.message", "event_id": "$r",
                   "sender": "@homer:test", "origin_server_ts": BASE_TS,
                   "content": {"msgtype": "m.text", "body": body}}]

        assert diary.resolve(events)[0].body == "He gets that from me."

    def test_a_bare_upload_has_no_caption(self):
        """An image's `body` is its filename until a caption displaces it."""
        events = [{"type": "m.room.message", "event_id": "$i",
                   "sender": "@marge:test", "origin_server_ts": BASE_TS,
                   "content": {"msgtype": "m.image", "body": "IMG_4021.png",
                               "url": "mxc://test/x"}}]

        assert diary.resolve(events)[0].body == ""

    def test_a_captioned_upload_keeps_the_caption(self):
        events = [{"type": "m.room.message", "event_id": "$i",
                   "sender": "@marge:test", "origin_server_ts": BASE_TS,
                   "content": {"msgtype": "m.image", "body": "Maggie's drawing",
                               "filename": "IMG_4021.png",
                               "url": "mxc://test/x"}}]

        assert diary.resolve(events)[0].body == "Maggie's drawing"


class TestBursts:
    """Which timestamps are arrival times rather than recording times."""

    def test_a_run_of_uploads_from_one_sender_is_a_burst(self):
        run = [_msg(event_id=f"$m{i}", ts=BASE_TS + i * SYNC_GAP_MS)
               for i in range(3)]

        marked = diary.mark_bursts(run, window_s=WINDOW_S)

        assert len({m.burst for m in marked}) == 1
        assert all(m.burst for m in marked)

    def test_a_message_on_its_own_is_not_a_burst(self):
        alone = [_msg(event_id="$a"), _msg(event_id="$b", ts=BASE_TS + 60_000)]

        assert [m.burst for m in diary.mark_bursts(alone, window_s=WINDOW_S)] \
            == [None, None]

    def test_a_different_sender_ends_the_run(self):
        run = [_msg(event_id="$a", sender="marge"),
               _msg(event_id="$b", sender="homer", ts=BASE_TS + SYNC_GAP_MS)]

        assert [m.burst for m in diary.mark_bursts(run, window_s=WINDOW_S)] \
            == [None, None]

    def test_the_window_decides_where_a_run_stops(self):
        """The same room splits differently under a different window.

        This is the property that makes the window a parameter: a
        replayed corpus and a real room disagree about what "together"
        means by three orders of magnitude.
        """
        run = [_msg(event_id="$a"), _msg(event_id="$b", ts=BASE_TS + 5_000)]

        assert all(m.burst for m in diary.mark_bursts(run, window_s=10))
        assert not any(m.burst for m in diary.mark_bursts(run, window_s=1))


# ── The trap the corpus was built to set ──────────────────────────────


class TestJoiningFragments:
    """One recording split in two looks exactly like two recordings."""

    def test_halves_that_agree_become_one_recording(self):
        entries = _compile()

        joined = _entry_for(entries, "fragment-urlaub-a")
        assert "$fragment-urlaub-b" in joined.event_ids
        assert joined.body.startswith("Hi kids, today is March twenty-second")
        assert joined.body.endswith("that's our surprise.")

    def test_a_burst_of_separate_memos_is_not_joined(self):
        """Three same-second uploads are usually three memos, not one.

        Timing alone cannot tell the two apart, so only the words may
        join them. A compiler that joined on adjacency would merge a
        month of the family's memos into one paragraph.
        """
        items = [i for i in _spec_items() if i.get("burst") == "sync-1"]
        entries = _compile(items)

        assert len(entries) == 3
        assert all(len(e.event_ids) == 1 for e in entries)


# ── Dating ────────────────────────────────────────────────────────────


class TestDating:
    """Which of the two clocks to believe, and when to admit neither."""

    def test_a_spoken_date_beats_the_servers_timestamp(self):
        sent_in_september = _msg(ts=BASE_TS, burst="burst-1")
        reading = diary.Reading(spoken_date="2026-03-16")

        on, confidence, _basis = diary.date_for(sent_in_september, reading)

        assert on == date(2026, 3, 16)
        assert confidence == "spoken"

    def test_a_message_sent_live_is_dated_from_its_timestamp(self):
        on, confidence, _ = diary.date_for(_msg(), diary.Reading())

        assert on == datetime.fromtimestamp(
            BASE_TS / 1000, timezone.utc).date()
        assert confidence == "sent"

    def test_a_silent_memo_in_a_burst_admits_it_does_not_know(self):
        """The one case where the compiler must not produce a date.

        Its timestamp is known to be days off and it never said when it
        was made, so any date here would be invented. A family diary
        with a confidently wrong date is worse than one with a gap.
        """
        on, confidence, basis = diary.date_for(
            _msg(burst="burst-1"), diary.Reading())

        assert confidence == "uncertain"
        assert "sync burst" in basis
        assert on == datetime.fromtimestamp(
            BASE_TS / 1000, timezone.utc).date()


class TestAgainstCorpusGroundTruth:
    """Scored against the dates the corpus says are true."""

    def test_every_spoken_date_is_recovered(self):
        items = _spec_items()
        entries = _compile(items)

        spoken = [i for i in items if i["date_source"] == "spoken"]
        assert spoken, "the corpus should carry spoken-date items"
        for item in spoken:
            entry = _entry_for(entries, item["id"])
            assert entry.on == item["true_date"], item["id"]
            assert entry.confidence == "spoken", item["id"]

    def test_a_fragments_second_half_inherits_the_spoken_date(self):
        items = _spec_items()
        inherited = next(i for i in items
                         if i["date_source"] == "inherited-from-fragment")

        entry = _entry_for(_compile(items), inherited["id"])

        assert entry.on == inherited["true_date"]

    def test_the_unrecoverable_memo_is_the_only_uncertain_entry(self):
        items = _spec_items()
        entries = _compile(items)

        unrecoverable = [i["id"] for i in items
                         if i["date_source"] == "unrecoverable"]
        uncertain = [e for e in entries if e.confidence == "uncertain"]

        assert len(uncertain) == len(unrecoverable)
        for item_id in unrecoverable:
            assert _entry_for(entries, item_id).confidence == "uncertain"


# ── Composing entries ─────────────────────────────────────────────────


class TestEntries:
    """What ends up being one thing on the page."""

    def test_a_late_caption_belongs_to_its_photo(self):
        entries = _compile()

        photo = _entry_for(entries, "bild-zeichnung")
        assert photo.kind == "image"
        assert photo.comments, "the caption should hang off the photo"
        assert "Maggie drew this today" in photo.comments[0][1]
        with pytest.raises(AssertionError):
            # It is not an entry of its own.
            assert _entry_for(entries, "bild-zeichnung-caption").kind == "text"

    def test_a_reply_to_a_memo_is_filed_under_that_memo(self):
        entries = _compile()

        memo = _entry_for(entries, "memo-bart-zeugnis")
        assert [who for who, _ in memo.comments] == ["homer"]
        assert memo.on == date(2026, 3, 16), \
            "a reply months later must not move the memo's date"

    def test_entries_of_one_day_keep_the_order_the_room_had(self):
        entries = _compile()
        same_day = [e for e in entries if e.on == REPLAYED_ON.date()]

        assert same_day, "the replay files undated items on its own date"
        assert [e.at for e in same_day] == sorted(e.at for e in same_day)


# ── The page ──────────────────────────────────────────────────────────


class TestRendering:
    """The diary quotes; it never summarises."""

    def test_an_uncertain_entry_says_so_where_it_is_read(self):
        entries = _compile()
        uncertain = next(e for e in entries if e.confidence == "uncertain")

        page = diary.render_month([uncertain])

        assert "[!warning]" in page
        assert "sync burst" in page

    def test_a_photo_without_a_caption_says_so_rather_than_naming_a_file(self):
        page = diary.render_month([diary.Entry(
            on=date(2026, 4, 3), confidence="sent", basis="dated from when it "
            "was sent", kind="image", sender="homer", body="")])

        assert "No caption came with this one." in page
        assert ".png" not in page

    def test_a_speaker_is_not_addressed_to_themselves(self):
        """A misread addressee must not become a dedication."""
        page = diary.render_month([diary.Entry(
            on=date(2026, 3, 28), confidence="sent", basis="b", kind="voice",
            sender="homer", body="words", addressee="Homer")])

        assert "for Homer" not in page
        assert "### Homer" in page

    def test_the_words_reach_the_page_unaltered(self):
        spoken = ("Hi Bart, today is March 16th. I won't forget that.")
        page = diary.render_month([diary.Entry(
            on=date(2026, 3, 16), confidence="spoken", basis="b", kind="voice",
            sender="marge", body=spoken, addressee="Bart")])

        assert spoken in page

    def test_the_index_lists_a_page_for_every_month(self):
        entries = _compile()

        index = diary.render_index(entries)

        for key in {diary.month_key(e.on) for e in entries}:
            assert f"]({key})" in index

    def test_pages_are_named_for_the_months_they_cover(self):
        pages = diary.pages_for(_compile())
        paths = [path for path, _body, _title in pages]

        assert "diary/index.md" in paths
        assert "diary/2026-03.md" in paths
        assert all(p.startswith("diary/") for p in paths)
