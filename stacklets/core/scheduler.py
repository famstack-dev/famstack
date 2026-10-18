"""The stack's scheduler: runs stack commands when their entries say.

Runs on the host inside core's API process (`famstack-api.py`), which
launchd keeps alive: the one host service that runs the stack CLI, on
request and on time. Every `TICK` seconds it reads the entries and
`stack.toml` fresh, runs what is due, records what happened
(`stack.schedule`), and notes the check in a heartbeat that `stack doctor`
reads. Entries are edited with `stack core schedule`.

A run is the stack CLI in text mode, in its own thread, with the entry's
time limit. An entry never overlaps itself: still running at its next due
time, that due time is skipped. What a run does is the command's business;
the scheduler keeps the first lines of its output in the run log.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
import tomllib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

from stack.plugins import stacklet_commands  # noqa: E402
from stack.schedule import OUTPUT_LINES, Entry, Schedule, decide, refusal  # noqa: E402
from stack.stack import Stack  # noqa: E402

STACK_BIN = REPO_ROOT / "stack"
# Seconds between checks: a run starts at most this late.
TICK = 30


class Scheduler:
    """One instance's scheduler: what is due, and the runs in flight.

        scheduler = Scheduler(instance_dir=Path("~/famstack"), data_dir=Path("~/famstack-data"))
        scheduler.check()            # once; the service calls it every TICK seconds
    """

    def __init__(self, instance_dir: Path, data_dir: Path, *, stack_bin: Path = STACK_BIN):
        self.instance_dir, self.data_dir, self.stack_bin = instance_dir, data_dir, stack_bin
        self.schedule = Schedule(data_dir / "core" / "schedule")
        self._running: set[str] = set()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def check(self, now: datetime | None = None) -> None:
        tz = self._timezone()
        now = now or datetime.now(tz)
        last_due = self.schedule.last_due()
        entries = self.schedule.entries()
        finished = []
        for name, entry in entries.items():
            try:
                decision = decide(entry, last_due.get(name), now, tz)
            except ValueError as e:
                self._record(entry, now, "refused", output=str(e))
                continue
            for due in decision.missed:
                self._record(entry, due, "missed")
            if decision.last:
                last_due[name] = decision.last
            if decision.run_for:
                self._start(entry, decision.run_for)
            if decision.finished:
                finished.append(name)
        self.schedule.save_last_due(last_due)
        if finished:
            # Read again: the CLI may have changed an entry while this ran.
            current = self.schedule.entries()
            self.schedule.save({n: e for n, e in current.items()
                                if n not in finished or e.when != entries[n].when})

    def wait(self) -> None:
        """Until the runs started so far are done (for tests and shutdown)."""
        for thread in list(self._threads):
            thread.join()

    def run_now(self, entry: Entry, due: datetime) -> dict:
        """Run an entry in this thread and return its record (`schedule run`)."""
        return self._run(entry, due)

    # ── Running ───────────────────────────────────────────────────────────

    def _start(self, entry: Entry, due: datetime) -> None:
        with self._lock:
            busy = entry.name in self._running
            self._running.add(entry.name)
        if busy:
            self._record(entry, due, "skipped", output="the run before is still going")
            return
        thread = threading.Thread(target=self._run, args=(entry, due), daemon=True,
                                  name=f"schedule-{entry.name}")
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()

    def _run(self, entry: Entry, due: datetime) -> dict:
        started = datetime.now(due.tzinfo)
        try:
            words = shlex.split(entry.run)
            if why := refusal(words, stacklet_commands(self._stack())):
                return self._record(entry, due, "refused", started=started, output=why)
            env = {**os.environ, "STACK_SCHEDULE_NAME": entry.name,
                   "STACK_SCHEDULE_FOR": entry.person, "STACK_DIR": str(self.instance_dir)}
            try:
                done = subprocess.run([str(self.stack_bin), *words], cwd=str(self.instance_dir),
                                      env=env, capture_output=True, text=True,
                                      timeout=entry.timeout)
            except subprocess.TimeoutExpired:
                return self._record(entry, due, "timeout", started=started,
                                    output=f"stopped after {entry.timeout} s")
            output = (done.stdout or done.stderr or "").strip()
            return self._record(entry, due, "ok" if done.returncode == 0 else "failed",
                                started=started, exit=done.returncode, output=output)
        finally:
            with self._lock:
                self._running.discard(entry.name)

    def _record(self, entry: Entry, due: datetime, status: str, *, started=None, exit=None,
                output: str = "") -> dict:
        run = {"name": entry.name, "due": due.isoformat(), "status": status, "run": entry.run,
               "started": started.isoformat() if started else None,
               "ended": datetime.now(due.tzinfo).isoformat() if started else None,
               "exit": exit, "output": "\n".join(output.splitlines()[:OUTPUT_LINES])}
        with self._lock:
            self.schedule.record(**run)
        return run

    # ── The instance, read fresh ─────────────────────────────────────────

    def _config(self) -> dict:
        try:
            return tomllib.loads((self.instance_dir / "stack.toml").read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return {}

    def _timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self._config().get("core", {}).get("timezone") or "UTC")
        except (KeyError, ValueError):
            return ZoneInfo("UTC")

    def _stack(self) -> Stack:
        return Stack(REPO_ROOT, self.data_dir, instance_dir=self.instance_dir)


    @classmethod
    def for_instance(cls, instance_dir: Path) -> "Scheduler":
        """The scheduler of the instance at `instance_dir`, its data dir read
        from `stack.toml` the way the CLI reads it."""
        try:
            core = tomllib.loads((instance_dir / "stack.toml").read_text()).get("core", {})
        except (OSError, tomllib.TOMLDecodeError):
            core = {}
        name = core.get("name", "stack")
        return cls(instance_dir, Path(core.get("data_dir", f"~/{name}-data")).expanduser())

    def serve(self, stop: threading.Event) -> None:
        """Check every TICK seconds until `stop` is set; one bad check never
        stops the clock."""
        print(f"scheduler: checking {self.schedule.dir} every {TICK} s", file=sys.stderr, flush=True)
        while not stop.is_set():
            try:
                self.check()
                self.schedule.beat()
            except Exception as e:
                print(f"scheduler: check failed: {e}", file=sys.stderr, flush=True)
            stop.wait(TICK)


def start(instance_dir: Path) -> threading.Event:
    """Run the instance's scheduler in a background thread; set the returned
    event to stop it."""
    stop = threading.Event()
    scheduler = Scheduler.for_instance(instance_dir)
    threading.Thread(target=scheduler.serve, args=(stop,), daemon=True, name="scheduler").start()
    return stop
