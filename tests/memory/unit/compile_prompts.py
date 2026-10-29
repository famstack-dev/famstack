"""The wiki compile's prompts on a fabricated family, measured once per test run.

`test_memory_compile_prompts.py` asserts on these measurements; this module
makes them. A measurement takes about 40 seconds at 500 filings, so it runs
in the background from the moment the unit lane has collected the tests
(`start`, called by memory's conftest), one process per size, while the
workers run every other test. The tests themselves are collected last and
read the result (`wait_for`), so no worker sits waiting for another. A
measurement that failed leaves its error, which every test reports, rather
than being made again.

Run it by hand for the baseline table of one size:

    PYTHONPATH=lib python -m tests.memory.unit.compile_prompts 500 /tmp/prompts

The scenarios, on one memory per size, in this order:

    seed        the history, the curator's first boot, then a night
    document    (1) one new document
    diary       (2) one new diary entry
    correction  (3) one card corrected
    quiet       a night with nothing filed since the scenarios above
    next day    (4) the night after it, nothing filed either
    tick        (6) a list item ticked with `stack memory list tick`
    burst       (5) 40 filings at once

Each step is the curator's loop in a process of its own, with the
environment its container has (`RunningMemory.curators_loop`): a push is
pulled, mirrored, compiled at once, then compiled with the model once the
quiet window has passed; a night is the curator's sweep (reconcile, the
lists' archive, the diary's full pass, the rebuild). The model is
`instance.Model`, answering each page with a valid answer for its kind.

The memories room has no stand-in, so the diary's full pass at night reads
nothing and writes nothing, as when Synapse is down: a diary month is
compiled only when a push brings one of its records.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

SIZES = (50, 500)
SCENARIOS = ("seed", "document", "diary", "correction", "quiet", "next day", "tick", "burst")
CHARS_PER_TOKEN = 3
BLOCK = 2_048  # tokens: what the rig's model server caches a prompt in (design §1)
# The topic page that shows the groceries list's open items: the list names the topic.
CAMPING = "family/camping/about.md"
# Longer than a 2,000-filing measurement takes, so only a hung one runs into it.
_MOST_SECS = 900
_REPO = Path(__file__).resolve().parents[3]
# This run's id, when no xdist worker gives one (a run without workers).
_RUN = uuid.uuid4().hex


def tokens(prompt: str) -> int:
    return len(prompt) // CHARS_PER_TOKEN


# ── What a measurement is ───────────────────────────────────────────────

@dataclass
class Prompts:
    """Every model request of each scenario, as `(page, prompt)`, and the
    camping topic page as the brain has it before and after the tick."""

    calls: dict[str, list[tuple[str, str]]]
    camping: dict[str, str]

    def of(self, scenario: str, page: str) -> str:
        return next(p for name, p in self.calls[scenario] if name == page)

    def to_json(self) -> str:
        return json.dumps({"calls": self.calls, "camping": self.camping})

    @classmethod
    def from_json(cls, text: str) -> "Prompts":
        data = json.loads(text)
        return cls({k: [tuple(c) for c in v] for k, v in data["calls"].items()}, data["camping"])


def page_of(prompt: str) -> str:
    """Which page a prompt asks for: `home`, `person:<name>`, `topic:<folder>`,
    `diary:<month>`. Read off the prompt's opening, where each kind names its page."""
    if prompt.startswith("You write the home page"):
        return "home"
    if m := re.search(r"^This page is about: (.+)\.$", prompt, re.M):
        return f"person:{m.group(1)}"
    if m := re.match(r'You write the page of one topic[^"]*"([^"]+)"', prompt):
        return f"topic:{m.group(1)}"
    if m := re.match(r"Write the opening paragraph of a family's diary page for (.+)\.", prompt):
        return f"diary:{m.group(1)}"
    return "unknown"


def prompt_of(messages: list[dict]) -> str:
    return "\n".join(m["content"] for m in messages if isinstance(m["content"], str))


def answer_for(messages: list[dict]) -> str:
    """A valid answer for the kind of page `messages` ask for, citing its first source."""
    kind, _, name = page_of(prompt_of(messages)).partition(":")
    if kind == "diary":
        return f"A month of the Simpsons, {name}."
    return json.dumps({
        "home": {"family": "The Simpsons", "welcome": "The Simpsons of Springfield [1]."},
        "person": {"tagline": "A Simpson.", "about": "As the cards say [1]."},
        "topic": {"title": name, "summary": "As the cards say [1].", "facts": [], "state": []},
    }.get(kind, {}))


# ── Making one ──────────────────────────────────────────────────────────

def measure(size: int, root: Path) -> Prompts:
    """The scenarios on the fabricated family of `size` filings, under `root`."""
    from pytest_httpserver import HTTPServer

    from tests.memory.unit.fabricated_family import fabricated_family
    from tests.memory.unit.instance import running_memory
    from tests.model import Model

    family = fabricated_family(size)
    bundle = family.history(root / f"{size}.bundle")
    # On 127.0.0.1, as the lane pins `httpserver`: `localhost` can be ::1,
    # where another process may answer.
    server = HTTPServer(host="127.0.0.1")
    server.start()
    model = Model(server)
    model.answering(answer_for)
    calls: dict[str, list[tuple[str, str]]] = {}
    camping: dict[str, str] = {}

    def scenario(name: str, *steps) -> None:
        start = len(model.requests)
        for step in steps:
            step()
        calls[name] = [(page_of(prompt_of(m)), prompt_of(m)) for m in model.requests[start:]]

    try:
        with running_memory(root / "instance", {}, history=bundle, model=model.url) as memory:
            camping = play(family, memory, scenario)
    finally:
        server.clear()
        server.stop()
    return Prompts(calls, camping)


def play(family, memory, scenario, *, timeout: float = 600) -> dict[str, str]:
    """The scenarios in order on `memory`, which holds `family`'s history,
    each as `scenario(name, *steps)`: the caller runs the steps and measures
    around them. Returns the camping topic page as the brain has it before
    and after the tick. The quality eval with a real model plays the same
    ones (`tests/e2e/eval/wiki_compile.py`), with a longer `timeout` for each
    step of the curator's loop."""
    from tests.memory.unit.fabricated_family import TODAY

    camping: dict[str, str] = {}

    def on(day) -> None:
        # The host's commands (`stack memory list`) read the day as
        # the curator's do: this process is the admin's shell.
        os.environ["STACK_TODAY"] = day.isoformat()
        memory.on_day(day.isoformat())

    def loop(step: str):
        return lambda: memory.curators_loop(step, timeout=timeout)

    def filed_then_turns(*filings) -> None:
        filed(memory, list(filings))
        memory.curators_loop("turns", timeout=timeout)

    def tick() -> None:
        camping["before"] = (memory.brain / CAMPING).read_text(encoding="utf-8")
        answer = memory.person("marge").runs(
            "memory", "list", ["tick", "groceries", "Milk", "--as", "marge"])
        assert "error" not in answer, answer
        memory.curators_loop("turns", timeout=timeout)
        camping["after"] = (memory.brain / CAMPING).read_text(encoding="utf-8")

    on(TODAY)
    scenario("seed", loop("boot"), loop("night"))
    scenario("document", lambda: filed_then_turns(family.document(TODAY)))
    scenario("diary", lambda: filed_then_turns(family.diary_entry(TODAY)))
    last_note = next(f for f in reversed(family.filings) if f.kind == "note")
    scenario("correction", lambda: filed_then_turns(family.corrected(last_note, TODAY)))
    on(TODAY + timedelta(days=1))
    scenario("quiet", loop("night"))
    on(TODAY + timedelta(days=2))
    scenario("next day", loop("night"))
    scenario("tick", tick)
    scenario("burst", lambda: filed_then_turns(*family.burst(40, TODAY + timedelta(days=2))))
    return camping


def filed(memory, filings) -> None:
    """`filings` committed in order, each by its filer on its day (UTC) with
    the cards its writer files with it, and pushed to Forgejo at once, as a
    clone that was offline pushes them."""
    from tests.memory.unit.instance import clone_of, git

    person = memory.forgejo.repo.parent / "filer"
    if not person.exists():
        clone_of(memory.forgejo, person)
    git(person, "pull", "-q", "--ff-only")
    for f in filings:
        for path, text in f.files.items():
            (person / path).parent.mkdir(parents=True, exist_ok=True)
            (person / path).write_text(text, encoding="utf-8")
            git(person, "add", path)
        at = f"{f.on}T18:00:00+0000"
        subprocess.run(["git", "-C", str(person), "-c", f"user.name={f.by}",
                        "-c", f"user.email={f.by}@simpson", "commit", "-qm", f"File {f.path}"],
                       check=True, capture_output=True,
                       env={**os.environ, "GIT_AUTHOR_DATE": at, "GIT_COMMITTER_DATE": at})
    git(person, "push", "-q", "origin", "HEAD:main")


# ── Once per run, in the background ─────────────────────────────────────
#
# Each measurement runs in a process of its own, started by `start` in
# whichever process collects first. That process holds `<size>.lock` from
# before the start and hands it to the measurement, which holds it until it
# ends, so `wait_for` can tell a measurement still running from one that
# died. `stop`, at the end of the session, stops what still runs and removes
# the run's directory, the measurements' working copies inside it.

# The measurements this process started, so it can reap them in `stop`.
_started: list[subprocess.Popen] = []
# The process that leads the test run (`tests/conftest.py` sets it). A
# measurement whose run has ended without stopping it stops itself.
LEADER = "FAMSTACK_TEST_RUN_LEADER"
# How often a measurement looks whether its run still goes on.
_FOLLOW_SECS = 0.5


def run_dir() -> Path:
    """Where this run's measurements go: one directory every worker of the
    run finds (`PYTEST_XDIST_TESTRUNUID`, which the session sets for all of
    them, `tests/conftest.py`), a new one for each run."""
    run = os.environ.get("PYTEST_XDIST_TESTRUNUID") or _RUN
    return Path(tempfile.gettempdir()) / f"famstack-compile-prompts-{run}"


def start() -> None:
    """Start measuring every size in the background, once per run: the first
    caller makes the run's directory and starts them, any later one finds it."""
    try:
        run_dir().mkdir()
    except FileExistsError:
        return
    for size in SIZES:
        lock = os.open(run_dir() / f"{size}.lock", os.O_CREAT | os.O_WRONLY)
        fcntl.flock(lock, fcntl.LOCK_EX)
        with open(run_dir() / f"{size}.log", "w", encoding="utf-8") as log:
            measuring = subprocess.Popen(
                [sys.executable, "-s", "-m", "tests.memory.unit.compile_prompts",
                 str(size), str(run_dir())],
                cwd=_REPO, env={**os.environ, "PYTHONPATH": str(_REPO / "lib")},
                stdout=log, stderr=subprocess.STDOUT, pass_fds=(lock,),
                start_new_session=True)
        os.close(lock)  # the measurement holds it now, until it ends
        (run_dir() / f"{size}.pid").write_text(str(measuring.pid), encoding="utf-8")
        _started.append(measuring)


def wait_for(size: int) -> Prompts:
    """The measurement at `size`, once it is there; its error if it failed,
    or its log if it ended without either."""
    done, failed = run_dir() / f"{size}.json", run_dir() / f"{size}.error"
    deadline = time.monotonic() + _MOST_SECS
    while time.monotonic() < deadline:
        running = _running(size)
        if done.exists():
            return Prompts.from_json(done.read_text(encoding="utf-8"))
        if failed.exists():
            raise RuntimeError(f"measuring {size} filings failed:\n"
                               + failed.read_text(encoding="utf-8"))
        if not running:
            raise RuntimeError(f"measuring {size} filings ended without a result; its log:\n"
                               + _tail(run_dir() / f"{size}.log"))
        time.sleep(0.5)
    raise RuntimeError(f"measuring {size} filings took over {_MOST_SECS} s; its log:\n"
                       + _tail(run_dir() / f"{size}.log"))


def stop() -> None:
    """Stop the measurements that still run, with every process they
    started, and remove the run's directory. Called once the session ends,
    whether it passed, failed, stopped at the first failure or was
    interrupted."""
    if not run_dir().exists():
        return
    # A Ctrl-C can arrive twice (`uv run` passes on the one the terminal
    # already sent), and the second must not cut the cleaning short.
    interrupt = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        for measuring in _started:
            measuring.poll()  # one that ended is reaped, not waited on as a group
        for pid_file in run_dir().glob("*.pid"):
            _stop_group(int(pid_file.read_text(encoding="utf-8")))
        for measuring in _started:
            measuring.wait()
        _started.clear()
        shutil.rmtree(run_dir(), ignore_errors=True)
    finally:
        signal.signal(signal.SIGINT, interrupt)


def _running(size: int) -> bool:
    """Whether the measurement at `size` still holds its lock."""
    lock = os.open(run_dir() / f"{size}.lock", os.O_CREAT | os.O_WRONLY)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(lock, fcntl.LOCK_UN)
        return False
    finally:
        os.close(lock)


def _stop_group(pid: int) -> None:
    """End the process group `pid` leads: asked first, then made to.

    A group that is gone is done with. macOS answers `EPERM`
    (`PermissionError`), not `ESRCH`, for a group whose members are all
    zombies waiting to be reaped, so that counts as gone too: nothing in
    it runs any more.
    """
    for sig, grace in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            try:
                os.killpg(pid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.05)


def _tail(log: Path, lines: int = 40) -> str:
    try:
        return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "(no log)"


def _follow_the_run(leader: int, into: Path) -> None:
    """End this measurement, with every process it started, once the run's
    leader is gone: a run interrupted twice or killed never reaches `stop`.
    The run's directory is removed by a process of its own once no
    measurement of the run is left to write into it (`_REMOVER`)."""
    def follow() -> None:
        while True:
            time.sleep(_FOLLOW_SECS)
            try:
                os.kill(leader, 0)
            except ProcessLookupError:
                subprocess.Popen([sys.executable, "-c", _REMOVER, str(into)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                os.killpg(0, signal.SIGKILL)
    threading.Thread(target=follow, daemon=True).start()


# Removes a run's directory once every measurement group named in it
# (`<size>.pid`) has ended, within a minute.
_REMOVER = """
import os, shutil, sys, time
run = sys.argv[1]
def alive(pid):
    # macOS answers EPERM for a group of zombies only: gone, as in `_stop_group`.
    try:
        os.killpg(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True
pids = [int(open(os.path.join(run, f)).read()) for f in os.listdir(run) if f.endswith(".pid")]
deadline = time.monotonic() + 60
while time.monotonic() < deadline and any(alive(p) for p in pids):
    time.sleep(0.2)
shutil.rmtree(run, ignore_errors=True)
"""


def _measure_into(size: int, into: Path) -> None:
    """Measure `size` and leave `<size>.json`, or `<size>.error` with the
    trace; then print its report. Its working copies are under `into`."""
    work = into / f"work-{size}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        found = measure(size, work)
        partial = into / f"{size}.json.part"
        partial.write_text(found.to_json(), encoding="utf-8")
        partial.rename(into / f"{size}.json")
    except BaseException:
        (into / f"{size}.error").write_text(traceback.format_exc(), encoding="utf-8")
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(report({size: found}))


# ── The report ──────────────────────────────────────────────────────────

def report(measured: dict[int, Prompts]) -> str:
    """The prompt size of each page per scenario, in estimated tokens."""
    lines = []
    for size, prompts in measured.items():
        lines += ["", f"### {size} filings (estimated tokens per call)", "",
                  "| page | " + " | ".join(SCENARIOS) + " |",
                  "|---|" + "---:|" * len(SCENARIOS)]
        pages = sorted({page for calls in prompts.calls.values() for page, _ in calls},
                       key=lambda p: (p.split(":")[0] != "home", p))
        for page in pages:
            cells = [", ".join(str(tokens(p)) for name, p in prompts.calls[s] if name == page)
                     or "-" for s in SCENARIOS]
            lines.append(f"| {page} | " + " | ".join(cells) + " |")
        lines.append("| calls | " + " | ".join(str(len(prompts.calls[s])) for s in SCENARIOS)
                     + " |")
        lines.append("")
        for page in ("home", "person:Marge Simpson"):
            before, after = prompts.of("seed", page), prompts.of("document", page)
            same = shared_prefix(before, after)
            lines.append(f"After one document, {page} repeats {same:,} of the previous "
                         f"prompt's {len(before):,} characters ({tokens(before[:same]) // BLOCK} "
                         f"whole {BLOCK:,}-token blocks); its sources end at "
                         f"{end_of_sources(before):,}.")
    return "\n".join(lines)


def shared_prefix(before: str, after: str) -> int:
    return len(os.path.commonprefix([before, after]))


def end_of_sources(prompt: str) -> int:
    """Where the last numbered source (`[12] 2026-10-01 · ...`) of `prompt` ends."""
    starts = [m.start() for m in re.finditer(r"^\[\d+\] ", prompt, re.M)]
    if not starts:
        return 0
    end = prompt.find("\n\n", starts[-1])
    return len(prompt) if end < 0 else end


if __name__ == "__main__":
    from stack.stacklet_code import import_stacklets

    # As every entry point does: the stacklets are the package `stacklets`.
    import_stacklets([_REPO / "stacklets"])
    Path(sys.argv[2]).mkdir(parents=True, exist_ok=True)
    if leader := os.environ.get(LEADER):
        _follow_the_run(int(leader), Path(sys.argv[2]))
    _measure_into(int(sys.argv[1]), Path(sys.argv[2]))
