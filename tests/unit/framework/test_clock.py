"""`stack.clock`: the day a command works on, and the zone it counts in.

A compile, a list's tick and archive, an answer and the diary read the day
from one place: the household's zone, or the day a test sets with
`STACK_TODAY`. A bad setting must never stop a command, since the nightly
sweep runs unattended.

The instant is pinned at 23:30 UTC on 1 October 2026: the 1st in UTC, the
2nd in Kiritimati (UTC+14) and still the 1st in Honolulu (UTC-10). The
machine's own zone is set to Kiritimati, so a rule that fell back to local
time instead of UTC, or the other way round, gives a different day.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from stack import clock

INSTANT = datetime(2026, 10, 1, 23, 30, tzinfo=timezone.utc).timestamp()
FIRST, SECOND = date(2026, 10, 1), date(2026, 10, 2)


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    """The instant pinned, the machine's zone Kiritimati, nothing set."""
    monkeypatch.setattr(clock, "_now", lambda: INSTANT)
    monkeypatch.setattr(clock, "_warned", False)
    monkeypatch.delenv("STACK_TODAY", raising=False)
    monkeypatch.delenv("TIMEZONE", raising=False)
    local = os.environ.get("TZ")
    os.environ["TZ"] = "Pacific/Kiritimati"
    time.tzset()
    yield
    if local is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = local
    time.tzset()


def test_a_set_day_is_the_day(monkeypatch):
    """Given `STACK_TODAY`, when a command asks for today, then it gets that day."""
    monkeypatch.setenv("STACK_TODAY", "2026-09-30")

    assert clock.today() == date(2026, 9, 30)


def test_today_is_the_date_in_the_households_zone(monkeypatch):
    """Given `TIMEZONE`, when a command asks for today, then it gets the date
    in that zone, not the machine's: midnight is the family's midnight."""
    monkeypatch.setenv("TIMEZONE", "Pacific/Honolulu")

    assert clock.today() == FIRST


def test_an_unknown_zone_reads_as_utc(monkeypatch):
    """Given a `TIMEZONE` the system does not know, then the day is UTC's,
    never the machine's local one."""
    monkeypatch.setenv("TIMEZONE", "Springfield/Evergreen")

    assert clock.zone() is timezone.utc
    assert clock.today() == FIRST


def test_without_a_zone_the_machine_keeps_the_time():
    """Given no `TIMEZONE`, then the machine's own zone counts: the Mac's on
    the host, where it is the household's."""
    assert clock.today() == SECOND


def test_a_day_that_is_not_a_date_warns_once_and_uses_the_real_date(monkeypatch, capsys):
    """Given a `STACK_TODAY` that is not a date, when a command asks for today
    twice, then it gets the real date both times and one warning naming the
    setting."""
    monkeypatch.setenv("STACK_TODAY", "tomorrow")
    monkeypatch.setenv("TIMEZONE", "UTC")

    days = [clock.today(), clock.today()]

    assert days == [FIRST, FIRST]
    warnings = capsys.readouterr().err.strip().splitlines()
    assert len(warnings) == 1 and "STACK_TODAY" in warnings[0]


def test_the_machines_zone_keeps_summer_time_when_read_in_winter(monkeypatch):
    """Given no `TIMEZONE` and a Mac in Berlin, when a memo sent on 2 July
    at 00:30 summer time is read in January, then it is still the 2nd: the
    zone is the named one, not the winter offset of the moment it is read."""
    os.environ["TZ"] = "Europe/Berlin"  # the fixture puts it back
    time.tzset()
    winter = datetime(2026, 1, 15, 12, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(clock, "_now", lambda: winter)
    memo = datetime(2026, 7, 1, 22, 30, tzinfo=timezone.utc)

    assert memo.astimezone(clock.zone()).date() == date(2026, 7, 2)


def test_without_tz_the_zone_is_the_one_the_system_links(monkeypatch, tmp_path):
    """Given neither `TIMEZONE` nor `TZ`, the zone is the one
    `/etc/localtime` links to, as macOS sets it."""
    os.environ.pop("TZ")  # the fixture puts it back
    link = tmp_path / "localtime"
    link.symlink_to("/var/db/timezone/zoneinfo/Pacific/Kiritimati")
    monkeypatch.setattr(clock, "_LOCALTIME", link)

    assert clock.zone() == ZoneInfo("Pacific/Kiritimati")


def test_a_system_with_no_named_zone_reads_as_utc(monkeypatch, tmp_path):
    os.environ.pop("TZ")  # the fixture puts it back
    monkeypatch.setattr(clock, "_LOCALTIME", tmp_path / "missing")

    assert clock.zone() is timezone.utc


@pytest.mark.parametrize(("setting", "reason"), [
    (None, "unset"), ("Pacific/Honolulu", "set"), ("Springfield/Evergreen", "unknown")])
def test_the_household_zone_says_why_it_is_that_one(monkeypatch, setting, reason):
    """The reason lets a command warn about an unset or unknown zone
    without reading `TIMEZONE` itself."""
    if setting:
        monkeypatch.setenv("TIMEZONE", setting)

    assert clock.household().reason == reason


def test_now_is_on_the_day_a_test_sets(monkeypatch):
    """Given `STACK_TODAY`, the household's now is on that day, at the time
    of day it is."""
    monkeypatch.setenv("STACK_TODAY", "2026-09-30")
    monkeypatch.setenv("TIMEZONE", "UTC")

    assert (clock.now().date(), clock.now().hour, clock.now().minute) == (date(2026, 9, 30), 23, 30)
