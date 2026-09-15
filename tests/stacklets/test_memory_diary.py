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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot"))
sys.path.insert(0, str(_REPO_ROOT / "tools" / "family-memories"))

import diary  # noqa: E402
import diary_store  # noqa: E402
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
    has to fall back.
    """
    return {
        f"${item['id']}": diary.Reading(
            mode="note" if item["kind"] == "text" else "monologue",
            spoken_date=(item["true_date"].isoformat()
                         if item["date_source"] == "spoken" else None),
        )
        for item in items
    }


def _links_from_spec(items: list[dict]):
    """The links between messages a correct reader would find.

    These are the judgments that need two messages in view at once, so
    the spec is where they come from: `fragment_of` names the halves of
    a split recording, and an `implicit-context` item is a remark about
    the last picture posted before it.
    """
    continues: dict[str, str] = {}
    refers_to: dict[str, str] = {}
    halves: dict[str, str] = {}
    last_image: str | None = None

    for item in items:
        event_id = f"${item['id']}"
        if fragment := item.get("fragment_of"):
            if earlier := halves.get(fragment):
                continues[event_id] = earlier
            halves[fragment] = event_id
        if item["pattern"] == "implicit-context" and last_image:
            refers_to[event_id] = last_image
        if item["kind"] == "image":
            last_image = event_id

    return continues, refers_to


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
    continues, refers_to = _links_from_spec(items)
    return diary.compile_entries(messages, _readings_from_spec(items),
                                 continues=continues, refers_to=refers_to)


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
                   "content": {"msgtype": "m.text", "body": body,
                               "m.relates_to": {
                                   "m.in_reply_to": {"event_id": "$p"}}}}]

        assert diary.resolve(events)[0].body == "He gets that from me."

    def test_an_entry_may_open_with_a_quote(self):
        """A leading blockquote is only a reply fallback on a reply.

        Stripping it from everything would eat the opening of any entry
        that starts by quoting something, which in a diary is a natural
        way to write.
        """
        body = "> the only thing we have to fear\n\nLisa said this today."
        events = [{"type": "m.room.message", "event_id": "$t",
                   "sender": "@marge:test", "origin_server_ts": BASE_TS,
                   "content": {"msgtype": "m.text", "body": body}}]

        assert diary.resolve(events)[0].body == body

    def test_a_video_is_not_dropped_on_the_floor(self):
        """A family posts a clip as readily as a photo."""
        events = [{"type": "m.room.message", "event_id": "$v",
                   "sender": "@marge:test", "origin_server_ts": BASE_TS,
                   "content": {"msgtype": "m.video", "body": "clip.mp4",
                               "url": "mxc://test/v"}}]

        resolved = diary.resolve(events)

        assert [m.kind for m in resolved] == ["video"]
        assert resolved[0].body == "", "a filename is not a caption"

    def test_a_client_that_repeats_the_filename_sends_no_caption(self):
        """Some clients set `filename` and `body` to the same string."""
        events = [{"type": "m.room.message", "event_id": "$i",
                   "sender": "@marge:test", "origin_server_ts": BASE_TS,
                   "content": {"msgtype": "m.image", "body": "IMG_4021.png",
                               "filename": "IMG_4021.png",
                               "url": "mxc://test/x"}}]

        assert diary.resolve(events)[0].body == ""

    def test_a_late_night_memo_belongs_to_the_night_it_was_recorded(self):
        """Day boundaries are the household's, not UTC's.

        Half past midnight in Berlin is still the previous day in UTC,
        so reading the clock in UTC files the memo under a heading the
        family would not recognise.
        """
        berlin = ZoneInfo("Europe/Berlin")
        recorded = datetime(2026, 3, 17, 0, 30, tzinfo=berlin)
        events = [{"type": "m.room.message", "event_id": "$n",
                   "sender": "@marge:test",
                   "origin_server_ts": int(recorded.timestamp() * 1000),
                   "content": {"msgtype": "m.text", "body": "still awake"}}]

        assert diary.resolve(events, zone=berlin)[0].sent_on == date(2026, 3, 17)
        assert diary.resolve(events)[0].sent_on == date(2026, 3, 16)

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


class TestBurstsInARealRoom:
    """Pinned at the default window, against timings a replay cannot have.

    The corpus is replayed, so it compresses day-scale gaps to seconds
    and has to be compiled with a tiny window. These cases use the
    shipped default and the spacing a real room has, because the
    question they answer -- does an ordinary evening get called
    undateable -- is the one the corpus cannot ask.
    """

    def _run(self, gap_s, count=3, **reading_kw):
        messages = diary.mark_bursts(
            [_msg(event_id=f"$m{i}", ts=BASE_TS + i * gap_s * 1000, body="x")
             for i in range(count)],
            window_s=diary.DEFAULT_BURST_WINDOW_S,
        )
        readings = {"$m0": diary.Reading(**reading_kw)} if reading_kw else {}
        return diary.compile_entries(messages, readings)

    def test_memos_recorded_one_after_another_keep_their_timestamps(self):
        """Three memos at the dinner table are not a sync burst.

        This is the common case in a real room, and calling it
        undateable would put a warning on most of the diary.
        """
        entries = self._run(gap_s=40)

        assert [e.confidence for e in entries] == ["sent", "sent", "sent"]

    def test_a_memo_that_contradicts_its_own_timestamp_condemns_its_run(self):
        """One spoken date days off its arrival proves a flushed queue."""
        entries = self._run(gap_s=40, spoken_date="2026-03-16")

        assert [e.confidence for e in entries] \
            == ["spoken", "uncertain", "uncertain"]

    def test_a_memo_synced_just_after_midnight_is_not_a_contradiction(self):
        """Recorded before midnight, received after, is an honest day of
        drift rather than evidence the timestamps are lying."""
        spoken = datetime.fromtimestamp(
            BASE_TS / 1000, timezone.utc).date() - timedelta(days=1)
        entries = self._run(gap_s=40, spoken_date=spoken.isoformat())

        assert [e.confidence for e in entries] == ["spoken", "sent", "sent"]

    def test_messages_far_apart_never_form_a_run(self):
        entries = self._run(gap_s=3600, spoken_date="2026-03-16")

        assert [e.confidence for e in entries] == ["spoken", "sent", "sent"]


class TestJoiningInARealRoom:
    def test_a_link_that_reaches_past_the_previous_message_is_refused(self):
        """A split recording is two adjacent uploads, always.

        A model that links across an intervening message has misread,
        and merging on it would fuse two unrelated memories into one
        entry. The room is the check on the reading.
        """
        messages = [
            _msg(event_id="$a", ts=BASE_TS, body="I was going to say"),
            _msg(event_id="$b", ts=BASE_TS + 60_000, body="something else"),
            _msg(event_id="$c", ts=BASE_TS + 120_000, body="and then"),
        ]

        groups = diary.join_fragments(messages, {"$c": "$a"})

        assert [len(g) for g in groups] == [1, 1, 1]

    def test_a_different_speaker_never_finishes_your_sentence(self):
        messages = [
            _msg(event_id="$a", sender="marge", ts=BASE_TS, body="I meant"),
            _msg(event_id="$b", sender="homer", ts=BASE_TS + 1000, body="to say"),
        ]

        groups = diary.join_fragments(messages, {"$b": "$a"})

        assert [len(g) for g in groups] == [1, 1]


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

    def test_a_remark_about_a_photo_is_filed_under_that_photo(self):
        """"The picture above is from the barbecue" carries no Matrix
        relation at all. Nothing in the event says what it is about, so
        the link can only come from reading the two together -- and once
        it does, the remark stops being a memory of its own.
        """
        entries = _compile()

        photo = _entry_for(entries, "bild-kontextlos")
        assert "$impliziter-kontext" in photo.event_ids
        assert "barbecue" in photo.comments[0][1]

    def test_a_reply_reaches_back_as_far_as_it_likes(self):
        """Pointing at a memory is not the same as being read as one.

        Replying to a memo from March is how the family corrects or adds
        to it, and they do that whenever they happen to reread it. The
        age guard exists for links the model inferred; a reply carries
        the family's own intent, so it attaches however old its parent
        is -- and the memory keeps March's date, because that is when it
        happened.
        """
        memo = _msg(event_id="$memo", sender="marge", ts=BASE_TS,
                    body="Hi Bart, today is March 16th.")
        correction = _msg(
            event_id="$fix", sender="marge", kind="text",
            ts=BASE_TS + 180 * 86_400_000, reply_to="$memo",
            body="It was Principal Skinner who called, not Mrs Krabappel.")

        entries = diary.compile_entries(
            [memo, correction], {"$memo": diary.Reading(spoken_date="2026-03-16")})

        assert len(entries) == 1
        assert entries[0].on == date(2026, 3, 16)
        assert "Principal Skinner" in entries[0].comments[0][1]

    def test_a_follow_up_months_later_keeps_its_own_page(self):
        """Related is not the same as subordinate.

        A recording ends "the cast comes off in four weeks"; a note four
        weeks later says it did. A reader sees a remark on the first.
        Filing it as one costs that note its own date, its own place in
        the month, and credits it as a reply nobody made.
        """
        memo = _msg(event_id="$memo", sender="homer", ts=BASE_TS,
                    body="the doctor says the cast comes off in four weeks")
        later = _msg(event_id="$later", sender="homer", kind="text",
                     ts=BASE_TS + 28 * 86_400_000,
                     body="Bart got his cast off today.")

        entries = diary.compile_entries(
            [memo, later], {}, refers_to={"$later": "$memo"})

        assert len(entries) == 2
        assert not any(e.comments for e in entries)

    def test_a_caption_minutes_behind_its_photo_still_belongs_to_it(self):
        photo = _msg(event_id="$photo", kind="image", ts=BASE_TS, body="")
        caption = _msg(event_id="$cap", kind="text", ts=BASE_TS + 90_000,
                       body="Maggie drew this today.")

        entries = diary.compile_entries(
            [photo, caption], {}, refers_to={"$cap": "$photo"})

        assert len(entries) == 1
        assert entries[0].comments[0][1] == "Maggie drew this today."

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

    def test_every_kind_of_upload_is_named_in_plain_words(self):
        """A kind with no name falls through to the internal word and
        prints "video" in a line of otherwise written English."""
        for kind, expected in (("image", "Photo"), ("video", "Video"),
                               ("file", "File"), ("text", "Written note")):
            page = diary.render_month([diary.Entry(
                on=date(2026, 9, 13), confidence="sent", basis="b", kind=kind,
                sender="bart", body="")])
            assert expected in page, kind

    def test_a_photo_without_a_caption_says_so_rather_than_naming_a_file(self):
        page = diary.render_month([diary.Entry(
            on=date(2026, 4, 3), confidence="sent", basis="dated from when it "
            "was sent", kind="image", sender="homer", body="")])

        assert "Nothing was written alongside this one." in page
        assert ".png" not in page

    def test_an_untranscribable_recording_says_so(self):
        """A gated voice entry keeps its place; the page states why the
        words are missing instead of showing nothing."""
        page = diary.render_month([diary.Entry(
            on=date(2026, 9, 14), confidence="sent", basis="dated from when "
            "it was sent", kind="voice", sender="homer", body="")])

        assert "This recording could not be transcribed." in page

    def test_a_long_entry_renders_distilled_with_verified_quotes(self):
        """Long recordings render as gist + quotes + folded transcript,
        not as a wall of text."""
        body = " ".join(f"wort{i}." for i in range(130)) + \
            " Das ist der Satz der bleibt."
        page = diary.render_month([diary.Entry(
            on=date(2026, 9, 14), confidence="sent", basis="b",
            kind="voice", sender="marge", body=body,
            gist="Marge erzählt von einem langen Tag.",
            moments=["Das ist der Satz der bleibt."])])

        assert "Marge erzählt von einem langen Tag." in page
        assert "> [!quote] Das ist der Satz der bleibt." in page
        assert "> [!note]- Full transcript" in page
        # The body appears only inside the folded block, quoted.
        assert "\nwort0." not in page

    def test_a_short_entry_stays_verbatim_even_with_a_gist(self):
        page = diary.render_month([diary.Entry(
            on=date(2026, 9, 14), confidence="sent", basis="b",
            kind="voice", sender="marge", body="Kurz und wichtig.",
            gist="A gist that must not replace the words.")])

        assert "Kurz und wichtig." in page
        assert "A gist that must not replace the words." not in page


class TestTheKeptFileOnThePage:
    """A page that only links the room is worth nothing once the room's
    media store is gone. The compiler keeps the file and hands the
    renderer the path it landed on; this is what the page does with it.

    The embed syntax is Obsidian's and the wiki turns it into a player
    by reading the extension, which is why the extension in these
    assertions is the load-bearing part.
    """

    ROOM = "!memories:example.org"

    def _entry(self, kind: str, event_id: str = "$one") -> diary.Entry:
        return diary.Entry(
            on=date(2026, 3, 14), confidence="sent",
            basis="dated from when it was sent", kind=kind, sender="marge",
            body="", event_ids=[event_id])

    def test_a_recording_is_played_from_the_copy_that_plays_everywhere(self):
        """Safari does not decode Opus in Ogg, which is what a chat
        client records. The page embeds the AAC copy; the Ogg original
        stays in the archive."""
        page = diary.render_month(
            [self._entry("voice")], room_id=self.ROOM,
            media={"$one": "/media/2026/03/one.m4a"})

        assert "![[/media/2026/03/one.m4a]]" in page

    def test_a_photograph_is_shown_on_the_page(self):
        page = diary.render_month(
            [self._entry("image")], room_id=self.ROOM,
            media={"$one": "/media/2026/03/one.webp"})

        assert "![[/media/2026/03/one.webp]]" in page

    def test_a_document_is_offered_rather_than_embedded(self):
        """An embedded PDF is a scrolling frame that pushes the rest of
        the day off the screen."""
        page = diary.render_month(
            [self._entry("file")], room_id=self.ROOM,
            media={"$one": "/media/2026/03/one.pdf"})

        assert "[The file itself](/media/2026/03/one.pdf)" in page
        assert "![[" not in page

    def test_the_room_is_still_where_the_memory_lives(self):
        """The archive is a second copy, not a replacement. Following
        the entry back into the conversation it came from is the thing
        a family actually does with it."""
        page = diary.render_month(
            [self._entry("voice")], room_id=self.ROOM,
            media={"$one": "/media/2026/03/one.m4a"})

        assert f"[Listen in the room](https://matrix.to/#/{self.ROOM}/$one)" in page

    def test_each_half_of_a_split_recording_is_played(self):
        """A memo that arrived as two uploads is one entry with two
        files. Playing only the first would cut the memory in half."""
        entry = self._entry("voice")
        entry.event_ids = ["$first", "$second"]

        page = diary.render_month(
            [entry], room_id=self.ROOM,
            media={"$first": "/media/2026/03/first.m4a",
                   "$second": "/media/2026/03/second.m4a"})

        assert "![[/media/2026/03/first.m4a]]" in page
        assert "![[/media/2026/03/second.m4a]]" in page

    def test_an_entry_whose_file_could_not_be_kept_reads_as_it_always_did(self):
        """Every failure ends here: no homeserver, no disk, no ffmpeg.
        The page has to be exactly the page famstack published before
        the archive existed, or a bad night costs a family its diary."""
        entries = [self._entry("voice", "$a"), self._entry("image", "$b")]

        assert diary.render_month(entries, room_id=self.ROOM, media={}) == \
            diary.render_month(entries, room_id=self.ROOM)

    def test_only_the_events_that_were_kept_are_shown(self):
        entries = [self._entry("image", "$kept"), self._entry("image", "$lost")]

        page = diary.render_month(
            entries, room_id=self.ROOM,
            media={"$kept": "/media/2026/03/kept.webp"})

        assert page.count("![[") == 1


class TestVerifyMoments:
    """A quote reaches the page only when the body really contains it.
    The page shows the body's own text, not the model's copy."""

    BODY = ("Hallo Bart, heute ist der sechzehnte März. Direktor "
            "Skinner hat angerufen! Du hast der neuen Schülerin "
            "geholfen. Das vergesse ich dir nicht.")

    def test_an_exact_sentence_is_kept(self):
        assert diary.verify_moments(
            self.BODY, ["Direktor Skinner hat angerufen!"]) == \
            ["Direktor Skinner hat angerufen!"]

    def test_case_and_punctuation_differences_still_match_the_body(self):
        got = diary.verify_moments(
            self.BODY, ["direktor skinner hat angerufen"])
        assert got == ["Direktor Skinner hat angerufen!"]

    def test_a_run_of_sentences_matches_as_one_quote(self):
        got = diary.verify_moments(
            self.BODY,
            ["Du hast der neuen Schülerin geholfen. "
             "Das vergesse ich dir nicht."])
        assert got == ["Du hast der neuen Schülerin geholfen. "
                       "Das vergesse ich dir nicht."]

    def test_an_invented_quote_is_dropped(self):
        assert diary.verify_moments(
            self.BODY, ["Bart hat die Schule angezündet."]) == []

    def test_a_partial_sentence_is_dropped(self):
        assert diary.verify_moments(
            self.BODY, ["der neuen Schülerin"]) == []


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

    def test_a_month_opens_with_its_summary(self):
        entries = _compile()
        march = [e for e in entries if e.on.month == 3]

        page = diary.render_month(march, summary="A month of firsts.")

        assert page.startswith("# March 2026\n\nA month of firsts.")

    def test_a_month_without_a_summary_goes_straight_to_its_entries(self):
        """The summary is the only writing here that is not the family's,
        so its absence leaves the page shorter, never padded with
        boilerplate the reader sees on every other month."""
        entries = _compile()
        march = [e for e in entries if e.on.month == 3]

        page = diary.render_month(march)

        assert page.startswith("# March 2026\n\n## ")
        assert "memories room" not in page

    def test_a_summary_reaches_the_month_it_describes(self):
        pages = {path: body for path, body, _title in
                 diary.pages_for(_compile(),
                                 summaries={"2026-03": "Only March."})}

        assert "Only March." in pages["diary/2026/03.md"]
        assert "Only March." not in pages["diary/2026/04.md"]

    def test_the_index_lists_a_page_for_every_year(self):
        entries = _compile()

        index = diary.render_index(entries)

        for key in {diary.year_key(e.on) for e in entries}:
            assert f"]({key}/about)" in index

    def test_a_year_lists_its_months_and_who_is_in_them(self):
        entries = _compile()
        in_2026 = [e for e in entries if e.on.year == 2026]

        page = diary.render_year(in_2026)

        assert "## Months" in page
        assert "[March](03)" in page
        assert "recorded by Marge and Homer" in page

    def test_a_year_credits_only_who_recorded(self):
        """An addressee is the model's reading, not a fact about the
        household, so it never reaches a landing page."""
        page = diary.render_year([diary.Entry(
            on=date(2026, 3, 16), confidence="spoken", basis="b", kind="voice",
            sender="marge", body="words", addressee="Bart")])

        assert "recorded by Marge" in page
        assert "Bart" not in page

    def test_a_diary_nests_month_inside_year(self):
        """A diary outlives its first year, so months live under one.

        Flat `2026-03.md` files pile every month of every year into one
        folder, which is the explorer sidebar the family actually reads.
        """
        paths = [path for path, _body, _title in diary.pages_for(_compile())]

        assert "diary/about.md" in paths
        assert "diary/2026/about.md" in paths
        assert "diary/2026/03.md" in paths
        assert all(p.startswith("diary/") for p in paths)

    def test_a_folders_own_page_is_about_not_index(self):
        """Quartz renders a folder URL through a layout with no body in
        this wiki, so an `index.md` would be unreadable. Every other
        entity here is `about.md` for the same reason."""
        paths = [path for path, _body, _title in diary.pages_for(_compile())]

        assert not any(p.endswith("index.md") for p in paths)

    def test_a_month_page_is_titled_with_its_year(self):
        """The folder gives context in the sidebar; a link or a search
        result does not, so the title carries the year itself."""
        titles = {path: title for path, _body, title in
                  diary.pages_for(_compile())}

        assert titles["diary/2026/03.md"] == "March 2026"
        assert titles["diary/2026/about.md"] == "2026"


# ── What survives between runs ────────────────────────────────────────


class TestRememberingBetweenRuns:
    """The nightly must pay for what is new and nothing else.

    Deliberately not a watermark. The compiler re-reads the whole room
    every run, because a reply, an edit, or a remark arriving tonight
    can belong to an entry from years back, and anything walking forward
    from a last-processed id would never attach it. What is kept is what
    each message cost, keyed by the message.
    """

    def test_a_reading_survives_a_restart(self, tmp_path):
        readings, _ = diary_store.open_stores(tmp_path)
        readings.put("$a", {"mode": "monologue", "spoken_date": "2026-03-16",
                            "addressee": "Bart", "continues": None,
                            "refers_to": None}, "today is March 16th")
        readings.save()

        reopened, _ = diary_store.open_stores(tmp_path)

        assert reopened.get("$a", "today is March 16th")["spoken_date"] \
            == "2026-03-16"

    def test_a_better_transcript_is_read_again(self, tmp_path):
        """Give whisper the household's names and a memo it heard as
        "Part" comes back as "Bart". The reading taken from the old
        wording is now wrong about who was spoken to, so it has to go.
        """
        readings, _ = diary_store.open_stores(tmp_path)
        readings.put("$a", {"addressee": None},
                     "Part, today is July the 5th")

        assert readings.get("$a", "Part, today is July the 5th") is not None
        assert readings.get("$a", "Bart, today is July the 5th") is None

    def test_a_link_is_remembered_with_the_message_that_carries_it(self, tmp_path):
        """A slice read end to end is never sent again, so its links
        have to come back with it or a joined recording would split."""
        readings, _ = diary_store.open_stores(tmp_path)
        readings.put("$b", {"mode": "monologue", "continues": "$a",
                            "refers_to": None}, "and then")
        readings.save()

        reopened, _ = diary_store.open_stores(tmp_path)

        assert reopened.get("$b", "and then")["continues"] == "$a"

    def test_a_first_run_finds_an_empty_cache(self, tmp_path):
        readings, summaries = diary_store.open_stores(tmp_path / "nothing-here")

        assert len(readings) == 0
        assert readings.get("$a", "x") is None
        assert summaries.get("2026-03", "digest") == ""

    def test_a_corrupt_cache_is_not_a_broken_compile(self, tmp_path):
        """Paying for a reading twice beats refusing to run."""
        (tmp_path / "readings.json").write_text("{not json at all")

        readings, _ = diary_store.open_stores(tmp_path)

        assert readings.get("$a", "x") is None

    def test_an_unchanged_month_keeps_the_words_it_had(self, tmp_path):
        """Not only a saving. Re-summarising a settled month every night
        would reword the family's past while they slept.
        """
        _, summaries = diary_store.open_stores(tmp_path)
        summaries.put("2026-03", "abc", "A month of firsts.")

        assert summaries.get("2026-03", "abc") == "A month of firsts."

    def test_a_month_that_moved_is_written_again(self, tmp_path):
        _, summaries = diary_store.open_stores(tmp_path)
        summaries.put("2026-03", "abc", "A month of firsts.")

        assert summaries.get("2026-03", "xyz") == ""


class TestMonthDigest:
    def test_the_same_month_fingerprints_the_same(self):
        march = [e for e in _compile() if e.on.month == 3]

        assert diary.month_digest(march) == diary.month_digest(march)

    def test_a_reply_arriving_later_moves_the_month(self):
        """A memo from March can collect a remark in September. The
        March page has to be written again when it does.
        """
        march = [e for e in _compile() if e.on.month == 3]
        before = diary.month_digest(march)
        march[0].comments.append(("homer", "He gets that from me."))

        assert diary.month_digest(march) != before

    def test_a_memo_surfacing_late_moves_the_month_it_lands_in(self):
        march = [e for e in _compile() if e.on.month == 3]
        latecomer = diary.Entry(
            on=date(2026, 3, 30), confidence="spoken", basis="b",
            kind="voice", sender="marge", body="one more thing",
            at=BASE_TS, event_ids=["$late"])

        assert diary.month_digest(march + [latecomer]) \
            != diary.month_digest(march)


# ── Telling whisper who lives here ────────────────────────────────────


class TestSpokenVocabulary:
    """The only place a misheard name can be put right.

    The polish pass may not change words, and should not: the memories
    room holds what people said to their children. So the names go in
    before the audio is decoded rather than after.
    """

    def test_the_family_is_named(self):
        hint = diary.spoken_vocabulary(["Bart", "Lisa", "Maggie"])

        assert "Bart, Lisa and Maggie" in hint

    def test_topics_follow_the_people(self):
        hint = diary.spoken_vocabulary(["Marge"], ["camping", "the PTA"])

        assert hint.index("Marge") < hint.index("camping")

    def test_a_name_said_twice_is_written_once(self):
        hint = diary.spoken_vocabulary(["Bart", "Bart", "Lisa"])

        assert hint.count("Bart") == 1

    def test_nothing_known_is_no_hint_at_all(self):
        """An ungenerated vault must not send whisper an empty sentence
        to decode against."""
        assert diary.spoken_vocabulary([], []) == ""
        assert diary.spoken_vocabulary(["", "  "]) == ""

    def test_it_reads_as_speech_not_as_a_word_list(self):
        """Whisper treats the hint as preceding speech, so a bare list
        biases the decoder toward answering in lists."""
        hint = diary.spoken_vocabulary(["Homer"], ["camping"])

        assert hint.endswith(".")
        assert "The people in this family are Homer." in hint


class TestGermanRendering:
    """configure_language swaps every reader-facing string. English is
    the module default; tests restore it."""

    def test_a_german_page_has_no_english_strings(self):
        diary.configure_language("de")
        try:
            page = diary.render_month([diary.Entry(
                on=date(2026, 9, 14), confidence="sent",
                basis=diary._L["basis_sent"], kind="voice",
                sender="marge", body="", mode="dialogue",
                event_ids=["$ev1"])],
                room_id="!r:x")
        finally:
            diary.configure_language("en")
        assert "# September 2026" in page
        assert "Gespräch" in page
        assert "Diese Aufnahme konnte nicht transkribiert werden." in page
        assert "Im Chat anhören" in page
        for english in ("Conversation", "Listen in the room",
                        "could not be transcribed"):
            assert english not in page

    def test_german_month_names(self):
        diary.configure_language("de")
        try:
            page = diary.render_month([diary.Entry(
                on=date(2026, 3, 2), confidence="sent", basis="b",
                kind="text", sender="lisa", body="Hallo.")])
        finally:
            diary.configure_language("en")
        assert "# März 2026" in page
        assert "Montag, 2. März" in page


class TestPersonalMessagesPostWhole:
    """A message addressed to one person is a letter. It renders in
    full whatever its length; distillation never touches it."""

    def test_a_long_addressed_memo_is_not_distilled(self):
        body = " ".join(f"wort{i}" for i in range(150))
        page = diary.render_month([diary.Entry(
            on=date(2026, 9, 14), confidence="spoken", basis="b",
            kind="voice", sender="marge", body=body, addressee="Bart",
            gist="Should never render.", event_ids=["$ev1"])],
            room_id="!r:x")

        assert "wort0" in page
        assert "Should never render." not in page
        assert "Full transcript" not in page
        assert "— for Bart" in page


class TestHomeLink:
    """The pointer the diary hands to pages that link to it.

    The diary owns its own path and its own wording, so the wiki home
    page renders this string verbatim. Both halves are pinned here.
    """

    def test_points_at_the_diary_front_page(self):
        assert "(/family/diary/about)" in diary.home_link("family")

    def test_the_bucket_is_the_callers(self):
        """Diary paths are relative to the shared bucket, which is named
        in config. The compiler never reads it."""
        assert "(/office/diary/about)" in diary.home_link("office")

    def test_is_a_callout_so_it_reads_as_a_signpost(self):
        assert diary.home_link("family").startswith("> [!tip] ")

    def test_carries_the_diary_title_and_a_line_of_its_own(self):
        link = diary.home_link("family")
        assert "Family Diary" in link
        assert len(link.splitlines()) == 2

    def test_renders_in_the_household_language(self):
        diary.configure_language("de")
        try:
            link = diary.home_link("family")
            assert "Familientagebuch" in link
            assert "Recordings" not in link
        finally:
            diary.configure_language("en")


class TestEmptyDiary:
    """What the diary page says before anything has been recorded.

    This page is where the wiki's diary link lands, so on a fresh
    install it is the first thing a family reads about the feature. A
    count of zero teaches nobody anything; these tests pin that it
    explains how to record instead.
    """

    def test_it_says_how_to_record_something(self):
        page = diary.render_index([])

        assert "Memories" in page                 # names the room
        assert "voice message" in page.lower()
        assert "Press record." in page

    def test_it_keeps_the_diary_title_and_opening(self):
        """Not a separate error page: the same front door, with the
        years replaced by the note on how to fill them."""
        page = diary.render_index([])

        assert page.startswith("# Family Diary")
        assert "## Years" not in page

    def test_it_explains_the_two_things_that_are_not_obvious(self):
        """Speaking the date and replying to a message both change what
        the compiler does with a recording, and neither is guessable
        from the room."""
        page = diary.render_index([])

        assert "Today is the third of March" in page
        assert "Reply to a message" in page

    def test_it_is_written_in_the_household_language(self):
        diary.configure_language("de")
        try:
            page = diary.render_index([])
            assert "Noch nichts aufgenommen" in page
            assert "Nothing recorded yet" not in page
        finally:
            diary.configure_language("en")

    def test_a_compiled_diary_shows_years_instead(self):
        """The note is for the empty case only. One entry and the page
        goes back to being an index."""
        page = diary.render_index(_compile())

        assert "Press record." not in page
        assert "## Years" in page


class TestBotMessagesAreNotMemories:
    """A bot writes in the room; none of it belongs in the diary.

    `stack messages setup` posts a welcome into the memories room when
    it creates it. Without this filter that welcome is the first entry
    of every fresh install's diary, and the family's own diary opens
    with someone else's words.
    """

    def _event(self, sender: str, body: str, ts: int = 1_700_000_000_000):
        return {
            "type": "m.room.message",
            "event_id": f"${sender}-{ts}",
            "sender": sender,
            "origin_server_ts": ts,
            "content": {"msgtype": "m.text", "body": body},
        }

    def test_a_bot_message_is_dropped(self):
        messages = diary.resolve([
            self._event("@stacker-bot:home.local", "This is your family's..."),
        ])

        assert messages == []

    def test_the_family_still_comes_through(self):
        messages = diary.resolve([
            self._event("@stacker-bot:home.local", "welcome", ts=1),
            self._event("@marge:home.local", "Bart lost a tooth", ts=2),
        ])

        assert [m.sender for m in messages] == ["marge"]

    def test_a_person_whose_name_ends_in_bot_is_not_a_bot(self):
        """The convention is a localpart suffix on the whole account, so
        the check must not fire on a name that merely ends in it."""
        messages = diary.resolve([
            self._event("@abbot:home.local", "Bart lost a tooth"),
        ])

        assert [m.sender for m in messages] == ["abbot"]

