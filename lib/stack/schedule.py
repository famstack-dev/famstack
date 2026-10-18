"""The stack's scheduler: which stack command runs when, kept on disk.

An entry is a name, a time (`when`) and a stack command line (`run`):

    daily 07:00              every day at seven, household time
    weekly fri 17:00         Fridays at five
    at 2026-10-19 07:00      once
    in 10m                   once, ten minutes from when it was added

Household time is `[core] timezone`, wall clock: `daily 07:00` stays at
seven across a change to or from summer time.

The scheduler service (`stacklets/core/scheduler.py`) decides what is due
and runs it; the CLI (`stack core schedule`) edits the entries. Both use
this module. Standard library only: both run on the host.

What happened is kept beside the entries, so a restart neither repeats a
run nor forgets one. A due time that passed while the scheduler was down
or the Mac asleep runs once if it is within the entry's grace, otherwise
it is recorded as missed; of several passed due times only the latest can
run, so a weekend asleep sends Monday's briefing once.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

GRACE = 15 * 60       # seconds a passed due time still runs
TIMEOUT = 5 * 60      # seconds a run may take
OUTPUT_LINES = 20     # of a run's output kept in the log
# Due times computed at most per check, so an entry added years ago cannot spin.
_MAX_DUE_TIMES = 10_000

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_CLOCK = r"([01]?\d|2[0-3]):([0-5]\d)"
_DURATION = re.compile(r"^(\d+)([smhd])$")
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}


# ── When ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class When:
    """A parsed `when`: daily, weekly, or once (`at`)."""

    kind: str
    clock: time = time(0, 0)
    weekday: int = 0          # weekly only, Monday 0
    moment: datetime | None = None  # once only

    def next_after(self, after: datetime, tz: ZoneInfo) -> datetime | None:
        """The first due time later than `after`, or None when there is none."""
        if self.kind == "at":
            return self.moment if self.moment and self.moment > after else None
        local = after.astimezone(tz)
        day = local.date()
        for _ in range(8):
            if self.kind == "daily" or day.weekday() == self.weekday:
                due = datetime.combine(day, self.clock, tzinfo=tz)
                if due > after:
                    return due
            day += timedelta(days=1)
        return None

    @property
    def once(self) -> bool:
        return self.kind == "at"


def parse_when(text: str, tz: ZoneInfo, now: datetime) -> When:
    """`text` as a When; `in <duration>` becomes the moment it means from `now`.

    Raises ValueError with a sentence saying what was expected.
    """
    words = text.strip().lower().split()
    if len(words) == 2 and words[0] == "daily" and (m := re.fullmatch(_CLOCK, words[1])):
        return When("daily", clock=time(int(m[1]), int(m[2])))
    if len(words) == 3 and words[0] == "weekly" and words[1][:3] in _DAYS \
            and (m := re.fullmatch(_CLOCK, words[2])):
        return When("weekly", clock=time(int(m[1]), int(m[2])), weekday=_DAYS.index(words[1][:3]))
    if len(words) == 3 and words[0] == "at" and (m := re.fullmatch(_CLOCK, words[2])):
        try:
            day = date.fromisoformat(words[1])
        except ValueError:
            day = None
        if day:
            return When("at", moment=datetime.combine(day, time(int(m[1]), int(m[2])), tzinfo=tz))
    if len(words) == 2 and words[0] == "in":
        return When("at", moment=now + timedelta(seconds=parse_duration(words[1])))
    raise ValueError(f"'{text}' is no time the scheduler knows. Use 'daily 07:00', "
                     "'weekly fri 17:00', 'at 2026-10-19 07:00' or 'in 10m'.")


def parse_duration(text: str) -> int:
    """`90s`, `15m`, `2h`, `1d` or `0` as seconds."""
    if text.strip() == "0":
        return 0
    m = _DURATION.match(text.strip().lower())
    if not m:
        raise ValueError(f"'{text}' is no duration. Use a number and s, m, h or d: '15m', '2h'.")
    return int(m[1]) * _UNIT[m[2]]


def as_text(when: When) -> str:
    """The `when` as it is stored: `in` is kept as the moment it meant."""
    if when.once and when.moment:
        return f"at {when.moment:%Y-%m-%d %H:%M}"
    clock = f"{when.clock:%H:%M}"
    return f"daily {clock}" if when.kind == "daily" else f"weekly {_DAYS[when.weekday]} {clock}"


# ── Entries ──────────────────────────────────────────────────────────────

@dataclass
class Entry:
    name: str
    when: str
    run: str
    added: str
    title: str = ""           # what it does, in words; the command when empty
    by: str = "admin"         # who created it: a person, or the admin at the terminal
    grace: int = GRACE
    timeout: int = TIMEOUT
    person: str = ""          # who it is for

    @property
    def what(self) -> str:
        return self.title or self.run

    def matches(self, *, by: str = "", for_: str = "") -> bool:
        """Whether it was created by `by` and is for `for_`; an empty one matches all."""
        return ((not by or by.lower() == self.by.lower())
                and (not for_ or for_.lower() == self.person.lower()))

    def parsed(self, tz: ZoneInfo) -> When:
        return parse_when(self.when, tz, datetime.fromisoformat(self.added))


@dataclass
class Decision:
    """What a check found for one entry at one moment."""

    run_for: datetime | None = None
    missed: list[datetime] = field(default_factory=list)
    last: datetime | None = None
    finished: bool = False


def decide(entry: Entry, last_due: datetime | None, now: datetime, tz: ZoneInfo) -> Decision:
    """Which of the entry's due times since `last_due` (or since it was added)
    run now, which were missed, and whether a one-off is done."""
    when = entry.parsed(tz)
    anchor = last_due or datetime.fromisoformat(entry.added)
    passed: list[datetime] = []
    due = when.next_after(anchor, tz)
    while due is not None and due <= now and len(passed) < _MAX_DUE_TIMES:
        passed.append(due)
        due = when.next_after(due, tz)
    decision = Decision(finished=when.once and (bool(passed) or due is None))
    if not passed:
        return decision
    decision.last = passed[-1]
    # In UTC: two times in one zone subtract by wall clock, an hour off across summer time.
    late = now.astimezone(timezone.utc) - passed[-1].astimezone(timezone.utc)
    if late.total_seconds() <= entry.grace:
        decision.run_for, decision.missed = passed[-1], passed[:-1]
    else:
        decision.missed = passed
    return decision


def refusal(words: list[str], commands: dict[str, dict]) -> str | None:
    """Why a command line may not be scheduled, or None.

    Only stacklet commands (`stack <stacklet> <command> ...`) run, never the
    framework's (`up`, `down`, `destroy`): the rule `[agent] allow` follows.
    A schedule does not edit schedules.
    """
    if len(words) < 2 or words[0] not in commands or words[1] not in commands[words[0]]:
        return (f"'{' '.join(words[:2])}' is no stacklet command. A schedule runs "
                "'<stacklet> <command> ...', as listed by './stack <stacklet>'.")
    if words[:2] == ["core", "schedule"]:
        return "a schedule does not change schedules."
    return None


# ── On disk ──────────────────────────────────────────────────────────────

class Schedule:
    """The entries, what last ran, and the run log, in one directory.

        schedule = Schedule(Path("~/famstack-data/core/schedule").expanduser())
        schedule.save({**schedule.entries(), "morning": Entry(...)})
    """

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.entries_file = self.dir / "entries.toml"
        self.state_file = self.dir / "state.json"
        self.runs_file = self.dir / "runs.jsonl"
        self.heartbeat_file = self.dir / "heartbeat"

    def entries(self) -> dict[str, Entry]:
        try:
            table = tomllib.loads(self.entries_file.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return {}
        return {name: Entry(name=name, **fields) for name, fields in table.items()}

    def save(self, entries: dict[str, Entry]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        blocks = ["# The stack's scheduled commands. Edited by `stack core schedule`.\n"]
        for name, entry in sorted(entries.items()):
            fields = {k: v for k, v in asdict(entry).items() if k != "name"}
            blocks.append(f"[{json.dumps(name)}]\n" + "".join(
                f"{key} = {json.dumps(value, ensure_ascii=False)}\n" for key, value in fields.items()))
        _write(self.entries_file, "\n".join(blocks))

    def last_due(self) -> dict[str, datetime]:
        try:
            raw = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return {}
        return {name: datetime.fromisoformat(at) for name, at in raw.items()}

    def save_last_due(self, last: dict[str, datetime]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        _write(self.state_file, json.dumps({n: at.isoformat() for n, at in last.items()}, indent=1))

    def beat(self, now: datetime | None = None) -> None:
        """Note that the scheduler just checked; `stack doctor` reads it."""
        self.dir.mkdir(parents=True, exist_ok=True)
        _write(self.heartbeat_file, (now or datetime.now(timezone.utc)).isoformat())

    def heartbeat(self) -> datetime | None:
        """When the scheduler last checked, or None if it never has."""
        try:
            return datetime.fromisoformat(self.heartbeat_file.read_text().strip())
        except (OSError, ValueError):
            return None

    def record(self, **run) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.runs_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(run, ensure_ascii=False, default=str) + "\n")

    def runs(self, name: str = "", limit: int = 20) -> list[dict]:
        try:
            lines = self.runs_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        runs = [json.loads(line) for line in lines if line.strip()]
        return [r for r in runs if not name or r.get("name") == name][-limit:]


def _write(path: Path, text: str) -> None:
    """Replace a file whole, so a reader never sees half of it."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
