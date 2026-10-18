"""When a scheduled command is due, and what happens to times that passed.

The scheduler is to send a morning briefing at seven, once, also after a
restart and after a weekend with the Mac asleep. These tests pin the time
rules: household wall clock across summer time, what input is refused,
and which passed due times run, which are only recorded as missed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from stack.schedule import Entry, decide, parse_duration, parse_when

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 18, 22, 0, tzinfo=BERLIN)  # a Sunday


def _next(text: str, after: datetime = NOW) -> datetime | None:
    return parse_when(text, BERLIN, NOW).next_after(after, BERLIN)


class TestWhenItIsDue:

    def test_daily_is_the_next_time_of_day(self):
        assert _next("daily 07:00") == datetime(2026, 10, 19, 7, 0, tzinfo=BERLIN)
        assert _next("daily 23:30") == datetime(2026, 10, 18, 23, 30, tzinfo=BERLIN)

    def test_weekly_is_the_next_such_day(self):
        assert _next("weekly fri 17:00") == datetime(2026, 10, 23, 17, 0, tzinfo=BERLIN)
        assert _next("weekly sunday 23:00") == datetime(2026, 10, 18, 23, 0, tzinfo=BERLIN)

    def test_at_is_once_and_in_counts_from_now(self):
        assert _next("at 2026-10-19 07:00") == datetime(2026, 10, 19, 7, 0, tzinfo=BERLIN)
        assert _next("at 2026-10-19 07:00", after=datetime(2026, 10, 19, 8, 0, tzinfo=BERLIN)) is None
        assert _next("in 10m") == NOW + timedelta(minutes=10)

    def test_seven_stays_seven_across_summer_time(self):
        # Clocks go back on 2026-10-25: 07:00 is then UTC+1 instead of UTC+2.
        before = _next("daily 07:00", after=datetime(2026, 10, 24, 6, 0, tzinfo=BERLIN))
        assert before is not None
        after = _next("daily 07:00", after=before)
        assert after is not None
        assert (before.hour, after.hour) == (7, 7)
        # Same tzinfo subtracts by wall clock; the hours that really pass are in UTC.
        assert after.astimezone(UTC) - before.astimezone(UTC) == timedelta(hours=25)

    @pytest.mark.parametrize("text", ["at noon", "daily 7", "weekly someday 10:00",
                                      "in ten minutes", "hourly", "daily 25:00"])
    def test_a_time_it_does_not_know_is_refused_with_the_forms_it_does(self, text):
        with pytest.raises(ValueError, match="daily 07:00"):
            parse_when(text, BERLIN, NOW)

    def test_durations(self):
        assert [parse_duration(t) for t in ("90s", "15m", "2h", "1d", "0")] == [90, 900, 7200,
                                                                                86400, 0]
        with pytest.raises(ValueError):
            parse_duration("soon")


def _entry(when: str, added: datetime = NOW, grace: int = 15 * 60) -> Entry:
    return Entry(name="briefing", when=when, run="memory list show dates",
                 added=added.isoformat(), grace=grace)


class TestWhatRunsAfterATimeHasPassed:

    def test_a_due_time_runs_once(self):
        entry = _entry("daily 07:00")
        at_seven = datetime(2026, 10, 19, 7, 0, 20, tzinfo=BERLIN)

        first = decide(entry, None, at_seven, BERLIN)
        again = decide(entry, first.last, at_seven + timedelta(seconds=30), BERLIN)

        assert first.run_for == datetime(2026, 10, 19, 7, 0, tzinfo=BERLIN)
        assert again.run_for is None and again.missed == []

    def test_nothing_is_due_before_the_first_time(self):
        assert decide(_entry("daily 07:00"), None, NOW + timedelta(hours=1), BERLIN).run_for is None

    def test_a_time_passed_while_down_runs_within_the_grace(self):
        late = datetime(2026, 10, 19, 7, 10, tzinfo=BERLIN)

        assert decide(_entry("daily 07:00"), None, late, BERLIN).run_for is not None

    def test_past_the_grace_it_is_only_recorded_as_missed(self):
        late = datetime(2026, 10, 19, 9, 0, tzinfo=BERLIN)

        decision = decide(_entry("daily 07:00"), None, late, BERLIN)

        assert decision.run_for is None
        assert decision.missed == [datetime(2026, 10, 19, 7, 0, tzinfo=BERLIN)]

    def test_a_weekend_asleep_runs_a_daily_entry_once(self):
        friday = datetime(2026, 10, 16, 20, 0, tzinfo=BERLIN)
        monday = datetime(2026, 10, 19, 7, 5, tzinfo=BERLIN)

        decision = decide(_entry("daily 07:00", added=friday), None, monday, BERLIN)

        assert decision.run_for == datetime(2026, 10, 19, 7, 0, tzinfo=BERLIN)
        assert len(decision.missed) == 2  # Saturday and Sunday

    def test_a_one_off_is_finished_once_its_time_passed(self):
        entry = _entry("at 2026-10-18 22:10")

        assert not decide(entry, None, NOW, BERLIN).finished
        assert decide(entry, None, NOW + timedelta(minutes=11), BERLIN).finished
