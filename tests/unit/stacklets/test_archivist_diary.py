"""The archivist in the memories room: quiet, except about the diary.

The memories room is the family's own. The archivist posts each diary
card as a notice in a thread on the entry's first message, once a day,
and one notice saying how many memories were added. A reply in a card's
thread corrects the card. Anything else the family posts there is theirs
and gets no answer: no filing, no search, no welcome, no reaction.

Routing is driven from the outside with Matrix shapes taken from the
spec (the same fixtures as `test_archivist_corrections.py`). The daily
job calls `stack memory diary` in a subprocess; here that boundary is
stood in by a report in the shape the CLI prints, and the rig runs the
real one.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

import diary_room  # noqa: E402
from archivist import ArchivistBot, _t  # noqa: E402
from test_archivist_corrections import FakeMatrix, _message, _thread_relation  # noqa: E402

BOT_ID = "@archivist-bot:server"
MARGE = "@marge:server"
ROOM_ID = "!memories:server"


def _t_en(key, **kw):
    return _t("en", key, **kw)


def _card(entry_id="e1", *, posted=False, at=1_800_000_000_000, **kw):
    card = {"entry_id": entry_id, "path": f"family/diary/entries/2026/09/2026-09-20-{entry_id}.md",
            "root": f"${entry_id}", "room_id": ROOM_ID, "date": "2026-09-20",
            "date_label": "Sunday, 20 September 2026", "date_basis": "spoken",
            "title": "Sandcastle at the lake", "summary": "Lisa and Bart built a sandcastle.",
            "facts": ["Place: lake"], "persons": ["Lisa", "Bart"], "tags": ["Family Life"],
            "state": "new", "posted": posted, "at": at}
    card.update(kw)
    return card


# ── When the job runs ─────────────────────────────────────────────────


class TestTheDailyJob:

    def _at(self, hour, minute=0, day=24):
        return datetime(2026, 9, day, hour, minute).astimezone()

    def test_not_before_its_time(self):
        assert not diary_room.job_due("07:30", "2026-09-23", self._at(7, 29))

    def test_at_its_time(self):
        assert diary_room.job_due("07:30", "2026-09-23", self._at(7, 30))

    def test_once_a_day(self):
        assert not diary_room.job_due("07:30", "2026-09-24", self._at(20))

    def test_a_mac_that_slept_through_the_morning_catches_up_when_it_wakes(self):
        assert diary_room.job_due("07:30", "2026-09-22", self._at(14))

    def test_a_malformed_time_never_runs(self):
        assert not diary_room.job_due("half past seven", "", self._at(14))

    def test_the_time_is_the_households_not_the_containers(self, monkeypatch):
        """The bot runner's clock is UTC. 07:30 means 07:30 where the family
        lives, and a day is their day."""
        monkeypatch.setenv("TIMEZONE", "Europe/Berlin")
        now = diary_room.household_now(datetime(2026, 9, 24, 5, 30, tzinfo=timezone.utc))
        assert (now.hour, now.minute) == (7, 30)
        assert now.date().isoformat() == "2026-09-24"
        late = diary_room.household_now(datetime(2026, 9, 24, 22, 30, tzinfo=timezone.utc))
        assert late.date().isoformat() == "2026-09-25"

    def test_without_a_household_timezone_it_uses_utc(self, monkeypatch):
        monkeypatch.delenv("TIMEZONE", raising=False)
        now = diary_room.household_now(datetime(2026, 9, 24, 5, 30, tzinfo=timezone.utc))
        assert now.hour == 5


# ── What gets announced ───────────────────────────────────────────────


class TestWhatIsAnnounced:

    def test_the_report_is_the_last_line_the_cli_prints(self):
        out = 'published family/diary/2026/09.md\n{"cards": [], "new": 0}\n'
        assert diary_room.read_report(out) == {"cards": [], "new": 0}
        assert diary_room.read_report("no report here") is None

    def test_a_card_whose_thread_has_its_notice_is_not_posted_again(self):
        fresh, older = diary_room.to_announce(
            {"cards": [_card("a", posted=True), _card("b")]}, since_ms=0)
        assert [c["entry_id"] for c in fresh] == ["b"] and older == 0

    def test_on_the_first_run_the_backlog_is_counted_not_posted(self):
        """A room with years of memories would otherwise get hundreds of
        threads in one morning."""
        fresh, older = diary_room.to_announce(
            {"cards": [_card("old", at=1_000), _card("new", at=5_000)]}, since_ms=2_000)
        assert [c["entry_id"] for c in fresh] == ["new"] and older == 1

    def test_the_card_reads_like_the_archivists_other_filings(self):
        """The same shape as a document's filing reply: title, one row of
        topics and people, the summary, the facts. No hashtags: a topic
        with a space in it breaks one."""
        text = diary_room.card_text(_card(), _t_en)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        assert lines[0] == "📔 **Sandcastle at the lake** · Sunday, 20 September 2026"
        assert lines[1] == "Family Life | Lisa | Bart"
        assert lines[2] == "Lisa and Bart built a sandcastle."
        assert lines[3] == "- Place: lake"
        assert "#" not in text

    def test_a_card_shows_at_most_five_facts(self):
        text = diary_room.card_text(_card(facts=[f"Fact: {i}" for i in range(8)]), _t_en)
        assert "Fact: 4" in text and "Fact: 5" not in text

    def test_the_first_cards_say_how_to_correct_them(self):
        assert "Reply here" in diary_room.card_text(_card(), _t_en, hint=True)
        assert "Reply here" not in diary_room.card_text(_card(), _t_en)

    def test_the_summary_explains_itself_the_first_times_then_keeps_to_the_count(self):
        first = diary_room.summary_text(3, explain=True, link="http://h/go/topic/diary", t=_t_en)
        later = diary_room.summary_text(6, explain=False, link="http://h/go/topic/diary", t=_t_en)
        one = diary_room.summary_text(1, explain=False, link="", t=_t_en)

        assert first.startswith("❤️ 3 new memories are in the family diary. Each has a card")
        assert first.endswith("[Open the diary](http://h/go/topic/diary)")
        assert later == "❤️ 6 new memories in the family diary. [Open the diary](http://h/go/topic/diary)"
        assert one == "❤️ 1 new memory in the family diary."


# ── The room ──────────────────────────────────────────────────────────


def _memories_room():
    return SimpleNamespace(room_id=ROOM_ID, canonical_alias="#memories:server", name=None,
                           users={uid: object() for uid in (BOT_ID, MARGE, "@homer:server")})


@pytest.fixture
def bot(tmp_path):
    bot = ArchivistBot(homeserver="http://homeserver", user_id=BOT_ID, password="x",
                       session_dir=tmp_path)
    bot._client = FakeMatrix()
    bot.sent: list[tuple] = []
    bot.routed: list[tuple] = []

    async def _send(room_id, text, reply_to=None, **kw):
        bot.sent.append((room_id, text, reply_to, kw))

    async def _react(room_id, event_id, emoji):
        bot.sent.append(("react", event_id, emoji))

    async def _record(*a, **kw):
        bot.routed.append(a)

    bot._send = _send
    bot._react = _react
    bot._handle_search = _record
    bot._handle_text_capture = _record
    bot._handle_reply_reprocess = _record
    bot._handle_reply_capture_reprocess = _record
    # A family could switch the room to react to everything. The memories
    # room must stay quiet anyway, so the tests grant it.
    bot._room_mode_allows_react = lambda _ctx: _granted()
    return bot


async def _granted():
    return True


def _card_thread(bot):
    """Marge's memo and the archivist's card notice in its thread."""
    client = bot._client
    client.add(_message("$memo", MARGE, "voice-message.ogg"))
    client.add(_message("$card", BOT_ID, "📔 Sandcastle",
                        content={"msgtype": "m.notice", **_thread_relation("$memo")},
                        envelope=diary_room.envelope(_card(root="$memo"),
                                                     diary_room.CARD_FILED, actor=BOT_ID)),
               thread_root="$memo")


class TestTheMemoriesRoomIsTheFamilys:

    @pytest.mark.asyncio
    async def test_a_memo_with_a_link_is_not_filed(self, bot):
        """Elsewhere a link is saved as a bookmark. Here it is part of a
        memory, and the diary keeps it with the rest."""
        memo = _message("$m", MARGE, "Photos from the zoo: https://photos.example.org/zoo")
        await bot._on_text(_memories_room(), memo)
        assert bot.sent == [] and bot.routed == []

    @pytest.mark.asyncio
    async def test_a_photo_is_not_filed(self, bot):
        event = _message("$p", MARGE, "IMG_1.jpg", content={
            "msgtype": "m.image", "url": "mxc://server/abc"})
        await bot._on_file(_memories_room(), event)
        assert bot.sent == [] and bot.routed == []

    @pytest.mark.asyncio
    async def test_no_welcome_is_posted(self, bot):
        room = _memories_room()
        await bot._send_room_welcome_if_needed(room, bot._room_context(room))
        assert bot.sent == []

    def test_a_voice_memo_is_left_to_the_diary(self, bot):
        """Decoding it would show the archivist typing under every memo."""
        memo = _message("$v", MARGE, "voice-message.ogg", content={"msgtype": "m.audio"})
        in_thread = _message("$w", MARGE, "voice-message.ogg",
                             content={"msgtype": "m.audio", **_thread_relation("$memo")})
        assert bot._wants_voice(_memories_room(), memo) is False
        assert bot._wants_voice(_memories_room(), in_thread) is True

    @pytest.mark.asyncio
    async def test_a_reply_in_a_card_thread_corrects_the_card(self, bot):
        _card_thread(bot)
        seen = []

        async def correct(room_id, event, found, words):
            seen.append((room_id, event.event_id, found["data"]["entry_id"], words))

        bot._diary.correct = correct
        event = _message("$fix", MARGE, "That was the 20th, and Bart built the tower.",
                         content=_thread_relation("$memo", falls_back_to="$card"))
        await bot._on_text(_memories_room(), event)

        assert seen == [(ROOM_ID, "$fix", "e1", "That was the 20th, and Bart built the tower.")]
        assert bot.routed == []

    @pytest.mark.asyncio
    async def test_a_thread_without_our_card_is_the_familys_conversation(self, bot):
        bot._client.add(_message("$memo", MARGE, "voice-message.ogg"))
        seen = []

        async def correct(*a):
            seen.append(a)

        bot._diary.correct = correct
        event = _message("$chat", "@homer:server", "So proud of her!",
                         content=_thread_relation("$memo"))
        await bot._on_text(_memories_room(), event)
        assert seen == [] and bot.sent == []


class TestTheDailyJobPostsWhatIsNew:

    @pytest.mark.asyncio
    async def test_the_how_to_is_shown_once_then_only_the_count(self, bot, monkeypatch):
        """The first announcement introduces the feature and how to correct
        a card. After that the count is enough."""
        import json as _json
        days = iter([
            {"room_id": ROOM_ID, "cards": [_card("a", at=2_000_000_000_000)]},
            {"room_id": ROOM_ID, "cards": [_card("b", at=2_000_000_000_000)]},
        ])

        async def cli(*args, timeout):
            return 0, _json.dumps(next(days)) + "\n", ""

        monkeypatch.setattr(diary_room, "_diary_cli", cli)
        await bot._diary.run_job(datetime(2026, 9, 24, 7, 30, tzinfo=timezone.utc))
        await bot._diary.run_job(datetime(2026, 9, 25, 7, 30, tzinfo=timezone.utc))

        first_card, first_summary, second_card, second_summary = [s[1] for s in bot.sent]
        assert "Reply here" in first_card and "card in the thread" in first_summary
        assert "Reply here" not in second_card
        assert second_summary.startswith("❤️ 1 new memory in the family diary.")

    @pytest.mark.asyncio
    async def test_cards_go_to_their_threads_and_one_summary_to_the_room(self, bot, monkeypatch):
        report = {"room_id": ROOM_ID, "new": 2, "kept_as_edited": [],
                  "cards": [_card("a", at=2_000_000_000_000),
                            _card("b", at=2_000_000_000_000),
                            _card("c", posted=True)]}

        async def cli(*args, timeout):
            import json
            return 0, json.dumps(report) + "\n", ""

        monkeypatch.setattr(diary_room, "_diary_cli", cli)
        bot.link_base_url = "http://h/go"
        await bot._diary.run_job(datetime(2026, 9, 24, 7, 30, tzinfo=timezone.utc))

        threads = [(s[2], s[3]["thread_root_event_id"], s[3]["msgtype"],
                    s[3]["metadata"]["dev.famstack.event"]["type"]) for s in bot.sent[:2]]
        assert threads == [("$a", "$a", "m.notice", "diary.filed"),
                           ("$b", "$b", "m.notice", "diary.filed")]
        room_id, summary, reply_to, kw = bot.sent[2]
        assert summary.startswith("❤️ 2 new memories are in the family diary.")
        assert reply_to is None and kw["msgtype"] == "m.notice"
        assert len(bot.sent) == 3

    @pytest.mark.asyncio
    async def test_a_failed_run_waits_before_it_tries_again(self, bot, monkeypatch):
        """The AI server being down at 07:30 must not start a compile every
        minute until it is back."""
        calls = []

        async def cli(*args, timeout):
            calls.append(args)
            return 1, "", "LLM unreachable"

        monkeypatch.setattr(diary_room, "_diary_cli", cli)
        start = datetime(2026, 9, 24, 7, 30, tzinfo=timezone.utc)
        await bot._diary.run_job(start)
        assert bot._diary.may_run(start + timedelta(minutes=10)) is False
        assert bot._diary.may_run(start + timedelta(minutes=31)) is True
        assert bot.sent == [] and len(calls) == 1

    @pytest.mark.asyncio
    async def test_a_day_with_nothing_new_is_silent(self, bot, monkeypatch):
        async def cli(*args, timeout):
            return 0, '{"room_id": "%s", "cards": [], "new": 0}\n' % ROOM_ID, ""

        monkeypatch.setattr(diary_room, "_diary_cli", cli)
        await bot._diary.run_job(datetime(2026, 9, 24, 7, 30, tzinfo=timezone.utc))
        assert bot.sent == []
