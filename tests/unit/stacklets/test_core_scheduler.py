"""The scheduler runs stack commands at their time, once, through restarts.

Driven the way an admin and the service drive it: `stack core schedule`
from the terminal, `Scheduler.check()` as the service's tick. The commands
are real ones of a small test stacklet in a temporary extension dir,
run through the real `./stack`: each run notes itself in a file.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from stack.schedule import Entry, Schedule
from stacklets.core.scheduler import Scheduler

REPO = Path(__file__).resolve().parents[3]

UTC = ZoneInfo("UTC")

NOTE = '''
HELP = "Note that it ran"
import os, sys, time
from pathlib import Path

def run(args, stacklet, config):
    if "slow" in args:
        time.sleep(3)
    with open(os.environ["NOTES"], "a") as f:
        f.write(" ".join(args) + "|" + os.environ.get("STACK_SCHEDULE_NAME", "") + "\\n")
    if "fail" in args:
        return {"error": "asked to fail"}
    return {"ok": True, "noted": " ".join(args)}
'''


@pytest.fixture
def instance(tmp_path, monkeypatch):
    extensions = tmp_path / "extensions"
    (extensions / "notes" / "cli").mkdir(parents=True)
    (extensions / "notes" / "stacklet.toml").write_text(
        'id = "notes"\nname = "Notes"\nversion = "0.1.0"\ncategory = "test"\n')
    (extensions / "notes" / "cli" / "write.py").write_text(NOTE)
    data = tmp_path / "data"
    (tmp_path / "stack.toml").write_text(
        f'[core]\nname = "stack"\ntimezone = "UTC"\ndata_dir = "{data}"\n'
        f'extension_dirs = ["{extensions}"]\n')
    (tmp_path / ".stack").mkdir()
    monkeypatch.setenv("NOTES", str(tmp_path / "notes.txt"))
    monkeypatch.setenv("STACK_DIR", str(tmp_path))
    return tmp_path


def _notes(instance) -> list[str]:
    path = instance / "notes.txt"
    return path.read_text().splitlines() if path.exists() else []


def _scheduler(instance) -> Scheduler:
    return Scheduler(instance, instance / "data")


def _schedule(instance) -> Schedule:
    return Schedule(instance / "data" / "core" / "schedule")


def _add(instance, when, *command, name="note", **fields):
    added = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)
    entry = Entry(name=name, when=when, run=" ".join(command), added=added.isoformat(), **fields)
    _schedule(instance).save({**_schedule(instance).entries(), name: entry})


def _check(instance, scheduler, at: str):
    scheduler.check(datetime.fromisoformat(at).replace(tzinfo=UTC))
    scheduler.wait()


def _stack(instance, *args):
    return subprocess.run([str(REPO / "stack"), *args], env={**os.environ},
                          capture_output=True, text=True, timeout=60)


class TestTheServiceRunsWhatIsDue:

    def test_a_due_entry_runs_its_command_once(self, instance):
        _add(instance, "daily 07:00", "notes write morning")
        scheduler = _scheduler(instance)

        _check(instance, scheduler, "2026-10-19T07:00:10")
        _check(instance, scheduler, "2026-10-19T07:00:40")

        assert _notes(instance) == ["morning|note"]
        assert [r["status"] for r in _schedule(instance).runs()] == ["ok"]

    def test_a_restart_does_not_repeat_a_run(self, instance):
        _add(instance, "daily 07:00", "notes write morning")
        _check(instance, _scheduler(instance), "2026-10-19T07:00:10")

        _check(instance, _scheduler(instance), "2026-10-19T07:01:00")  # a new process

        assert _notes(instance) == ["morning|note"]

    def test_a_framework_command_is_refused_when_it_is_due(self, instance):
        _add(instance, "daily 07:00", "down all")

        _check(instance, _scheduler(instance), "2026-10-19T07:00:10")

        run = _schedule(instance).runs()[-1]
        assert run["status"] == "refused" and "no stacklet command" in run["output"]

    def test_a_failing_command_is_logged_as_failed(self, instance):
        _add(instance, "daily 07:00", "notes write fail")

        _check(instance, _scheduler(instance), "2026-10-19T07:00:10")

        assert _schedule(instance).runs()[-1]["status"] == "failed"

    def test_an_entry_does_not_overlap_itself(self, instance):
        _add(instance, "daily 07:00", "notes write slow")
        scheduler = _scheduler(instance)
        scheduler.check(datetime(2026, 10, 19, 7, 0, 5, tzinfo=UTC))

        scheduler.check(datetime(2026, 10, 20, 7, 0, 5, tzinfo=UTC))  # still running
        scheduler.wait()

        assert [r["status"] for r in _schedule(instance).runs()] == ["skipped", "ok"]

    def test_a_run_that_takes_too_long_is_stopped(self, instance):
        _add(instance, "daily 07:00", "notes write slow", timeout=1)

        _check(instance, _scheduler(instance), "2026-10-19T07:00:10")

        assert _schedule(instance).runs()[-1]["status"] == "timeout"

    def test_a_one_off_is_gone_after_it_ran(self, instance):
        _add(instance, "at 2026-10-19 07:00", "notes write once")

        _check(instance, _scheduler(instance), "2026-10-19T07:00:10")

        assert _notes(instance) == ["once|note"]
        assert _schedule(instance).entries() == {}


class TestTheCommandLine:

    def test_add_list_run_log_remove(self, instance):
        added = _stack(instance, "core", "schedule", "add", "tea", "daily 16:00", "--",
                       "notes", "write", "tea")
        listed = _stack(instance, "core", "schedule", "list", "--json")
        ran = _stack(instance, "core", "schedule", "run", "tea")
        log = _stack(instance, "core", "schedule", "log", "--json")
        removed = _stack(instance, "core", "schedule", "remove", "tea")

        assert added.returncode == 0, added.stderr
        entry = json.loads(listed.stdout)["entries"][0]
        assert (entry["name"], entry["when"], entry["run"]) == ("tea", "daily 16:00",
                                                                "notes write tea")
        assert entry["next"].startswith((datetime.now(UTC) + timedelta(hours=0)).strftime("%Y"))
        assert ran.returncode == 0, ran.stdout + ran.stderr
        assert _notes(instance) == ["tea|tea"]
        assert [r["status"] for r in json.loads(log.stdout)["runs"]] == ["ok"]
        assert removed.returncode == 0 and _schedule(instance).entries() == {}

    def test_in_becomes_the_moment_it_means(self, instance):
        _stack(instance, "core", "schedule", "add", "tea", "in 10m", "notes", "write", "tea")

        assert _schedule(instance).entries()["tea"].when.startswith("at ")

    @pytest.mark.parametrize("when, command, says", [
        ("hourly", ["notes", "write"], "daily 07:00"),
        ("daily 07:00", ["destroy", "core"], "no stacklet command"),
        ("daily 07:00", ["core", "schedule", "list"], "does not change schedules"),
        ("at 2020-01-01 07:00", ["notes", "write"], "has passed"),
    ])
    def test_what_cannot_run_is_refused_before_it_is_written(self, instance, when, command, says):
        result = _stack(instance, "core", "schedule", "add", "x", when, "--", *command)

        assert result.returncode == 1
        assert says in result.stdout + result.stderr
        assert _schedule(instance).entries() == {}


class TestWhoAndWhat:
    """A schedule is read by person: what Homer set up, what is for
    Lisa, and in words, not as command lines."""

    def test_the_list_by_who_created_it_and_who_it_is_for(self, instance):
        _stack(instance, "core", "schedule", "add", "dentist", "at 2030-01-14 08:00",
               "--title", "Lisa's dentist tomorrow", "--by", "homer", "--for", "lisa",
               "--", "notes", "write", "dentist")
        _stack(instance, "core", "schedule", "add", "dates", "daily 07:00",
               "--by", "marge", "--", "notes", "write", "dates")

        def listed(*filter_):
            out = _stack(instance, "core", "schedule", "list", *filter_, "--json")
            return {e["name"]: e for e in json.loads(out.stdout)["entries"]}

        assert set(listed("--by", "homer")) == {"dentist"}
        assert set(listed("--for", "lisa")) == {"dentist"}
        assert set(listed("--by", "marge")) == {"dates"}
        assert set(listed("--by", "homer", "--for", "lisa")) == {"dentist"}
        assert set(listed("--by", "marge", "--for", "lisa")) == set()
        everything = listed()
        assert (everything["dentist"]["title"], everything["dentist"]["by"],
                everything["dentist"]["for"]) == ("Lisa's dentist tomorrow", "homer", "lisa")
        # Without a title, what it does is the command.
        assert everything["dates"]["title"] == "notes write dates"

    def test_an_entry_added_at_the_terminal_is_the_admins(self, instance):
        _stack(instance, "core", "schedule", "add", "tea", "in 10m", "notes", "write", "tea")

        assert _schedule(instance).entries()["tea"].by == "admin"
