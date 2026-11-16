"""Today's date and the time of day, as the household's clock has them.

A command that depends on the day (the wiki's schedule, a list's tick and
archive, an answer that says "yesterday", the diary's days, the curator's
nightly sweep) reads it here, not from `date.today()` or `time.localtime()`:

    from stack import clock
    clock.today()       # date(2026, 10, 28) in the household's zone
    clock.now()         # datetime(2026, 10, 28, 3, 31, tzinfo=ZoneInfo("Europe/Berlin"))
    clock.zone()        # ZoneInfo("Europe/Berlin") for TIMEZONE=Europe/Berlin

`TIMEZONE` is the household's zone, so midnight is the family's midnight.
A zone the system does not know reads as UTC. Without `TIMEZONE` the
system's own zone is the clock (`TZ`, else what `/etc/localtime` names):
the Mac's on the host, where it is the household's. It is always a named
zone, never the offset of the moment, so a summer date read in winter
keeps its summer time.

`STACK_TODAY` (`2026-10-28`) sets the day outright: a test sets it in the
environment it gives a command, as it sets the rest of that environment.
`STACK_NOW` (`2026-10-28T18:02:30+00:00`, with its zone) sets the moment,
for a command whose answer depends on the minute, such as whether a
message group in the diary is still open; `STACK_TODAY` still wins on
the day.
Standard library only, because the host CLI imports the framework too.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Where the instant comes from. A test of this module pins it, so a rule
# about the zone fails at any hour of the day it runs.
_now = time.time
# The link that names the system's zone on macOS and Linux.
_LOCALTIME = Path("/etc/localtime")

# The settings (`STACK_TODAY`, `STACK_NOW`) reported as bad, so a command
# that asks for the day many times warns once.
_warned: set[str] = set()


@dataclass(frozen=True)
class Household:
    """The household's zone, and why it is that one: `set` (from
    `TIMEZONE`), `unknown` (`TIMEZONE` names no zone the system knows, so
    UTC) or `unset` (no `TIMEZONE`, so the system's own). `name` is what
    `TIMEZONE` says."""

    zone: tzinfo
    reason: str
    name: str = ""


def household() -> Household:
    """The household's zone and the reason for it (see `Household`)."""
    name = os.environ.get("TIMEZONE", "").strip()
    if not name:
        return Household(_system_zone(), "unset")
    try:
        return Household(ZoneInfo(name), "set", name)
    except (ZoneInfoNotFoundError, ValueError):
        return Household(timezone.utc, "unknown", name)


def zone() -> tzinfo:
    """The household's zone (`household().zone`)."""
    return household().zone


def now() -> datetime:
    """The time in the household's zone, on the day `today()` gives."""
    current = datetime.fromtimestamp(_instant(), zone())
    day = today()
    return current if current.date() == day else datetime.combine(day, current.timetz())


def today() -> date:
    """`STACK_TODAY` when it is set and a date, else today in `zone()`.

    A `STACK_TODAY` that is not a date is reported once on stderr and the
    real date is used: a wrong clock setting must not stop a compile.
    """
    value = os.environ.get("STACK_TODAY", "").strip()
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            if "STACK_TODAY" not in _warned:
                _warned.add("STACK_TODAY")
                print(f"STACK_TODAY={value!r} is not a date (YYYY-MM-DD); using today's date",
                      file=sys.stderr)
    return datetime.fromtimestamp(_instant(), zone()).date()


def _instant() -> float:
    """`STACK_NOW` as epoch seconds when it is set and a moment with its
    zone, else the real instant. A bad value is reported once on stderr:
    a wrong clock setting must not stop a command."""
    value = os.environ.get("STACK_NOW", "").strip()
    if value:
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            moment = None
        if moment is not None and moment.tzinfo is not None:
            return moment.timestamp()
        if "STACK_NOW" not in _warned:
            _warned.add("STACK_NOW")
            print(f"STACK_NOW={value!r} is not a moment with its zone "
                  "(YYYY-MM-DDTHH:MM:SS+00:00); using the real time", file=sys.stderr)
    return _now()


def _system_zone() -> tzinfo:
    """The system's named zone: `TZ` when it names one, else the zone
    `/etc/localtime` links to; UTC when neither names one."""
    for name in (os.environ.get("TZ", "").lstrip(":"), _linked_zone()):
        if name:
            try:
                return ZoneInfo(name)
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return timezone.utc


def _linked_zone() -> str:
    """The zone name `/etc/localtime` links to (`.../zoneinfo/Europe/Berlin`), "" for none."""
    try:
        target = os.readlink(_LOCALTIME)
    except OSError:
        return ""
    return target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else ""
