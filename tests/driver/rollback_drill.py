#!/usr/bin/env python3
"""rollback_drill: rehearse an update and its rollback on a real Mac, then leave it as it was.

    # on this Mac: the build under test as a bundle, the driver and three letters to the test Mac
    git bundle create /tmp/drill.bundle HEAD v0.5.0-beta.1
    rsync -a --delete tests/driver/ <mac>:famstack-dev/rollback-drill-inputs/driver/
    # on the test Mac, as the user whose Docker the drill may use
    python3 ~/famstack-dev/rollback-drill-inputs/driver/rollback_drill.py \
        --bundle ~/famstack-dev/rollback-drill-inputs/drill.bundle \
        --letters ~/famstack-dev/rollback-drill-inputs/letters --report /tmp/rollback-drill-report

The inputs stay outside `~/famstack-dev/rollback-drill/`, the drill's own
folder, because the cleanup removes that folder and all in it. The
preflight refuses when it exists.

The unit tests of `stack snapshot` and `stack rollback` stand in for
Docker, git and APFS. This drill runs the real ones on a real install,
with fabricated Simpson data, and writes what happened into a report:
each command, its exit code, its time and the end of its output, a
verdict per criterion, the sizes of each snapshot and what each step
left on disk. It asserts nothing on its own exit code beyond "the run
reached its end"; the verdicts in the report are the result.

The run, in order:

1. Inventory of the Mac, and a refusal when another instance runs.
2. A clone of the build without a remote, and the drill's releases
   (`drill_release`): a real old release, the build, a broken build.
3. Install the old release with the family, start messages and docs,
   file two letters through the chat.
4. Update to the build and restart: the snapshot of a release without
   `stack rollback`, before Paperless migrates its database one way.
5. Kill the rollback back to the old release after each step that it
   records, until it passed the checkout; `stack up` must refuse in
   between. The old release then starts its stacklets with `stack up`,
   the admin guide's way out for a release without `stack rollback`.
6. On the build, `stack rollback` refuses the open record and names
   the old release; the guide's way out goes back to it, finishes the
   record there, and updates to the build again.
7. Update to the broken build. `stack up docs` must refuse. `stack
   restart` takes the snapshot, the migration empties the docs page.
8. One letter filed after the snapshot; one image tag moved by hand.
9. `stack rollback --yes`: the letters back, the release from before,
   the replaced state kept, the late letter named and still on disk,
   the moved tag back on the image that the snapshot names.
10. The cost of a large stacklet: 2 GB of random bytes, one snapshot.
11. The broken build once more, its restart, a kill inside git's
    checkout of the rollback, and the guide's steps.
12. `stack snapshot remove` of each entry: `.snapshots/` ends empty.
13. The cleanup and the second inventory (`drill_cleanup`). It runs
    also when a step fails, on SIGTERM and on SIGHUP, and before the
    tables of the report. `--only-until <n>` ends the run after the
    section with that number.

After each step the audit (`drill_audit`) lists what the step left, and
the end of the report judges each kind for criterion 9.

SAFETY: the drill refuses to start when a container named `stack-*`
exists, when `~/famstack-data` holds anything, or when its own folders
exist: it never moves or stops another instance. It never pushes, and its
clone has no remote (`drill_release`). It deletes only what it made: its
clone, what is in the data folder that it found absent or empty, the images that
the inventory before did not list, and the network `stack` when it was
absent before (`drill_cleanup`).

Debt: the report, the Docker reads and the
steps share this one file, with two module-level lists (`SIZES`,
`STALE`) as the run's state. Trigger: the next drill (an update drill
with another stacklet) or the next change of the report. Then split it
into `drill_report` and `drill_steps`.

The script uses only the standard library and the system's Python, as
the drill's Mac may have nothing else. The driver (`./driver`) files the
letters through the chat, as a family member does (AGENTS.md rule 11):
`./stack messages upload documents <letter> --as homer`, the command that
`driver as homer send` runs, called directly so that the drill's Mac
needs no Python environment for the driver's browser parts.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import tomllib
import traceback
import urllib.request
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import drill_audit as audit  # noqa: E402
import drill_cleanup as cleaning  # noqa: E402
import drill_inventory as inventory  # noqa: E402
import drill_release as releases  # noqa: E402

HOME = Path.home()
RUN_FOLDER = HOME / "famstack-dev"
DRILL_FOLDER = RUN_FOLDER / "rollback-drill"
CHECKOUT = DRILL_FOLDER / "famstack"
DATA = HOME / "famstack-data"
SNAPSHOTS = DATA / ".snapshots"

STACKLETS = ["core", "messages", "docs"]
LETTERS_BEFORE = ["homer-payslip.pdf", "lisa-report-card.pdf"]
LETTER_AFTER = "homer-car-insurance-policy.pdf"
FAMILY = """name = "Simpson"

[[people]]
name = "Homer Simpson"
words = "he"

[[people]]
name = "Marge Simpson"
words = "she"

[[people]]
name = "Lisa Simpson"
words = "she"
"""

# The tag that the drill moves by hand, and the image it moves it to: the
# docs stacklet's valkey, as a pull of a newer image would move it.
MOVED_TAG = "valkey/valkey:8.1-alpine"
MOVED_TO = "valkey/valkey:8.0-alpine"

FILING_TIMEOUT_S = 600
# Two steps of a rollback can follow each other within a few
# milliseconds, and a kill must land between them. The poll reads one
# small file, for the seconds that one round lasts.
POLL_S = 0.002
BIG_FILE = DATA / "docs" / "drill-2gb.bin"


# ── The report ───────────────────────────────────────────────────────────


@dataclass
class Done:
    """What one command did: its exit code, its output and its time."""

    code: int
    out: str
    seconds: float


class Report:
    """The run's report, written to disk after every line, so a crash keeps what came before.

        report.section("4. Update to the broken build")
        done = report.run(["./stack", "up", "docs"])
        report.verdict("7", done.code != 0 and "stack restart docs" in done.out, "stack up refuses")
    """

    def __init__(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self.path = folder / "report.md"
        self.path.write_text("# Rollback drill\n\n")
        self.verdicts: list[tuple[str, bool, str]] = []

    def line(self, text: str = "") -> None:
        """Append one line to the report and echo it."""
        with self.path.open("a") as out:
            out.write(text + "\n")
        try:
            print(text, flush=True)
        except OSError:
            pass   # stdout closed with the ssh connection: the file is the report

    def section(self, title: str) -> None:
        self.line(f"\n## {title}\n")

    def run(self, argv: list[str], cwd: Path = CHECKOUT, tail: int = 12, timeout: int = 1800,
            stdin: str | None = None, env: dict | None = None) -> Done:
        """Run one command, record it with its exit code, time and the last `tail` lines; never raises."""
        started = time.monotonic()
        try:
            # A session of its own: the git stand-in of step 9 kills the
            # process group of the rollback, which must not be the drill's.
            # No stdin unless given: a command that finds a terminal there
            # asks a question that nobody answers.
            given = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
            done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                                  env=env, start_new_session=True, **given)
            code, out = done.returncode, done.stdout + done.stderr
        except subprocess.TimeoutExpired as expired:
            code, out = 124, f"timed out after {timeout} s: {expired}"
        seconds = time.monotonic() - started
        shown = "\n".join(out.rstrip().splitlines()[-tail:])
        self.line(f"`$ {' '.join(argv)}` exit {code}, {seconds:.1f} s")
        if shown:
            self.line("```\n" + shown + "\n```")
        return Done(code, out, seconds)

    def verdict(self, criterion: str, ok: bool, text: str) -> None:
        """Record whether one criterion holds, with the evidence in `text`."""
        self.verdicts.append((criterion, ok, text))
        self.line(f"**{'holds' if ok else 'FAILS'} ({criterion})**: {text}")

    def table(self, header: list[str], rows: list[list[str]]) -> None:
        self.line("| " + " | ".join(header) + " |")
        self.line("|" + "---|" * len(header))
        for row in rows:
            self.line("| " + " | ".join(row) + " |")

    def save(self, name: str, data) -> None:
        (self.folder / name).write_text(json.dumps(data, indent=2, default=str))


# ── The instance: what the drill reads back ──────────────────────────────


def stack(report: Report, *args: str, **kw) -> Done:
    """`./stack <args>` in the drill's checkout, recorded."""
    return report.run(["./stack", *args], **kw)


def paperless_documents() -> list[dict] | None:
    """The documents the docs page lists, from Paperless's API with the stack's token; None when it fails.

    The token is the one the archivist files with (`docs__API_TOKEN` in
    `.stack/secrets.toml`, read only). It is read again on every call,
    because a rollback puts back the `.stack/` of its snapshot.
    """
    answer = paperless_get("/api/documents/?page_size=100")
    return None if answer is None else [{"id": d["id"], "title": d["title"]} for d in answer["results"]]


def paperless_get(path: str):
    """One GET of Paperless's API with the stack's token; the parsed answer, or None when it fails."""
    try:
        secrets = tomllib.loads((CHECKOUT / ".stack/secrets.toml").read_text())
        token = secrets["docs__API_TOKEN"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as failure:
        print(f"paperless: no token: {failure}", flush=True)
        return None
    request = urllib.request.Request(f"http://localhost:42020{path}", headers={"Authorization": f"Token {token}"})
    try:
        with urllib.request.urlopen(request, timeout=20) as answer:
            return json.load(answer)
    except Exception as failure:  # noqa: BLE001  the page's failure is what the drill reports
        print(f"paperless: {failure}", flush=True)
        return None


def documents_once_up(timeout_s: int = 300) -> list[dict] | None:
    """The documents the page lists, after waiting until Paperless answers; None when it never does."""
    wait_for("the docs page", lambda: paperless_documents() is not None, timeout_s)
    return paperless_documents()


def originals() -> list[str]:
    """The files in Paperless's archive folder of originals, as the drill reads it on disk."""
    folder = DATA / "docs/paperless/media/documents/originals"
    return sorted(p.name for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else []


def wait_for(what: str, ready, timeout_s: int = FILING_TIMEOUT_S) -> float | None:
    """Poll `ready()` every 5 s; the seconds it took, or None at the timeout."""
    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        if ready():
            return time.monotonic() - started
        time.sleep(5)
    print(f"timed out waiting for {what}", flush=True)
    return None


def file_letter(report: Report, letter: Path) -> None:
    """Homer sends one letter to the documents room, as a family member does.

    Homer, because the documents room holds the admin and the bots only.
    One more try after a pause, because right after `stack up messages`
    the room's alias can still be unknown to the server.
    """
    for _ in range(2):
        if stack(report, "messages", "upload", "documents", str(letter), "--as", "homer").code == 0:
            return
        time.sleep(15)


def container_images() -> dict[str, str]:
    """Each running `stack-*` container's image id."""
    out = subprocess.run(["docker", "ps", "--filter", "name=stack-", "--format", "{{.Names}}"],
                         capture_output=True, text=True).stdout.split()
    return {name: image_id(name, container=True) for name in sorted(out)}


def image_id(name: str, container: bool = False) -> str:
    """The image id of a tag, or of a container's image; "" when Docker does not know it."""
    argv = ["docker", "inspect", "--format", "{{.Image}}", name] if container else \
        ["docker", "image", "inspect", "--format", "{{.Id}}", name]
    return subprocess.run(argv, capture_output=True, text=True).stdout.strip()


def snapshot_names() -> list[str]:
    """The whole snapshots on disk, newest first."""
    if not SNAPSHOTS.is_dir():
        return []
    return sorted((p.name for p in SNAPSHOTS.iterdir() if (p / "MANIFEST.json").is_file()), reverse=True)


def new_snapshot(before: list[str]) -> str:
    """The newest snapshot that is not in `before`; "" when the command took none.

    A failed take leaves the newest snapshot an older one, so a step
    measures only the name its own command added.
    """
    added = [name for name in snapshot_names() if name not in before]
    return added[0] if added else ""


def replaced_names() -> list[str]:
    folder = SNAPSHOTS / "replaced"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def open_record() -> tuple[Path | None, list[str]]:
    """The newest rollback record and the steps it names done; (None, []) before the first."""
    records = sorted((SNAPSHOTS / "replaced").glob("*/ROLLBACK.json")) if SNAPSHOTS.is_dir() else []
    if not records:
        return None, []
    try:
        return records[-1], json.loads(records[-1].read_text())["done"]
    except (OSError, ValueError, KeyError):
        return records[-1], []   # caught between the write and the rename; the next poll reads it


def stop_and_start_gap(since: float, container: str = "stack-docs-db") -> str:
    """The seconds between the last `die` and the next `start` of `container` since `since`, from Docker's events."""
    out = subprocess.run(["docker", "events", "--since", str(int(since)), "--until", str(int(time.time()) + 1),
                          "--filter", f"container={container}", "--format", "{{.TimeNano}} {{.Action}}"],
                         capture_output=True, text=True, timeout=30).stdout.split("\n")
    died = started = None
    for line in out:
        if not line.strip():
            continue
        nanos, action = line.split(" ", 1)
        if action == "die":
            died = int(nanos)
        elif action == "start" and died is not None:
            started = int(nanos)
    return f"{(started - died) / 1e9:.1f} s" if died and started else "not seen"


# ── The steps ────────────────────────────────────────────────────────────


def preflight(report: Report) -> dict:
    """Refuse on any sign of another instance; the inventory before the first change."""
    report.section("1. Preflight and the inventory before")
    report.run(["docker", "context", "show"], cwd=HOME)
    others = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True,
                            text=True, check=True).stdout.split()
    found = cleaning.foreign_instances(others)
    if found:
        raise SystemExit(f"another instance has containers here: {found}. The drill moves nothing aside.")
    if DATA.exists() and any(DATA.iterdir()):
        raise SystemExit(f"{DATA} holds files of another instance. The drill moves nothing aside.")
    if DRILL_FOLDER.exists():
        raise SystemExit(f"{DRILL_FOLDER} exists: a drill ran before and was not cleaned up, "
                         "or the drill's inputs are in it, which the cleanup would delete.")
    users = subprocess.run(["ps", "-axo", "user=,comm="], capture_output=True, text=True).stdout
    orb_users = sorted({line.split()[0] for line in users.splitlines() if "OrbStack" in line})
    report.line(f"OrbStack runs for: {orb_users}")
    if any(user != os.environ.get("USER") for user in orb_users):
        raise SystemExit(f"another user's OrbStack runs: {orb_users}")
    before = inventory.take(RUN_FOLDER)
    before["data_existed"] = DATA.exists()   # empty, checked above, when it exists
    report.save("inventory-before.json", before)
    report.run(["docker", "system", "df"], cwd=HOME, tail=6)
    report.line(f"Inventory before: {len(before['containers'])} containers, {len(before['images'])} images, "
                f"networks {before['networks']}, run folder {before['run_folder']}, "
                f"{before['free_kb'] // 1024 // 1024} GB free")
    return before


def clone(report: Report, bundle: Path) -> None:
    """The build under test as a clone without a remote, and the drill's releases."""
    report.section("2. The clone and the drill's releases")
    DRILL_FOLDER.mkdir(parents=True, exist_ok=True)
    report.run(["git", "clone", "-q", str(bundle), str(CHECKOUT)], cwd=DRILL_FOLDER)
    report.run(["git", "remote", "remove", "origin"])
    made = releases.make(CHECKOUT)
    report.run(["git", "tag", "-l", "v0.5.0-*"])
    report.line(f"Releases: old {made.old}, build {made.build}, broken {made.broken}")


def install(report: Report, ai_url: str | None, ai_key: str | None) -> None:
    """Install the old release with the Simpsons and start messages and docs.

    The old release, because only a snapshot taken of it shows how a
    rollback ends on a release without `stack rollback`. The build is
    reached by `stack update`, as every beta tester reaches it, and its
    first restart migrates Paperless 3.0's database to 3.5, one way.
    """
    report.section("3. Install the old release, file two letters")
    report.run(["git", "checkout", "-q", "--detach", releases.OLD])
    family = DRILL_FOLDER / "simpsons.toml"
    family.write_text(FAMILY)
    stack(report, "install", "--family", str(family), "--yes", "--no-pair")
    if ai_url:
        stack(report, "ai", "switch", ai_url, "--key", "-", stdin=(ai_key or "") + "\n")
    stack(report, "up", "messages", "docs")
    stack(report, "version")


def file_letters_before(report: Report, letters: Path) -> list[dict]:
    """Two letters through the chat; the documents Paperless lists once both are filed."""
    for name in LETTERS_BEFORE:
        file_letter(report, letters / name)
    took = wait_for("two filed letters", lambda: len(paperless_documents() or []) >= len(LETTERS_BEFORE))
    documents = paperless_documents() or []
    report.line(f"Filed after {took} s: {documents}")
    if took is None:
        filing_evidence(report)
        raise RuntimeError("the letters were not filed; every later step needs them")
    return documents


def filing_evidence(report: Report) -> None:
    """What the report needs when a filing does not arrive: Paperless's tasks and the two logs."""
    tasks = paperless_get("/api/tasks/?page_size=10")
    report.line("Paperless's newest tasks: " + json.dumps(
        [{k: t.get(k) for k in ("task_file_name", "status", "result")} for t in (tasks if isinstance(tasks, list) else (tasks or {}).get("results", []))]))
    report.run(["docker", "logs", "--tail", "40", "stack-docs-paperless"], cwd=HOME, tail=40)
    report.run(["sh", "-c", "docker logs --tail 300 stack-core-bot-runner 2>&1 | grep -i archivist | tail -30"],
               cwd=HOME, tail=30)


def update_from_old(report: Report, filed: list[dict]) -> None:
    """Update the old release to the build and restart: the snapshot of a release without `stack rollback`."""
    report.section("4. Update from the old release to the build")
    stack(report, "update", releases.BUILD, "--yes")
    names = snapshot_names()
    free_before = audit.free_kb(DATA)
    since = time.time()
    restart = stack(report, "restart", timeout=2400)
    free_after = audit.free_kb(DATA)
    newest = new_snapshot(names)
    documents = documents_once_up()
    report.line(f"Restart exit {restart.code}; snapshot {newest}; stop to start of stack-docs-db "
                f"{stop_and_start_gap(since)}; the page lists {documents} (before: {filed})")
    report.line(f"`tmutil isexcluded {SNAPSHOTS}`: {audit.excluded_from_time_machine(SNAPSHOTS)}")
    measure(report, "update from the old release", newest, free_before, free_after, restart.seconds,
            disk="not the snapshot's: the same restart pulled and built the release's images")
    same_stacklets_again(report)
    stale_after(report, "the restart from the old release")


def same_stacklets_again(report: Report) -> None:
    """The disk's loss for a snapshot of the same stacklets, with no image pull in the same command.

    The restart's own number holds the release's images. A `stack
    snapshot create` right after it takes the same stacklets, so its loss
    is the snapshot's. The drill removes it again at once: the kills of
    the next step roll back to the newest snapshot, which must stay the
    old release's.
    """
    names = snapshot_names()
    free_before = audit.free_kb(DATA)
    done = stack(report, "snapshot", "create", *STACKLETS, timeout=2400)
    free_after = audit.free_kb(DATA)
    taken = new_snapshot(names) if done.code == 0 else ""
    measure(report, "the same stacklets, no image pull", taken, free_before, free_after, done.seconds)
    if taken:
        stack(report, "snapshot", "remove", taken, "--yes")


def kill_at_each_step(report: Report) -> None:
    """Kill the rollback back to the old release after each step it records, then the guide's way out.

    Each round starts `stack rollback --yes`, polls its record, and kills
    the whole process group as soon as the record gained a step. Between
    two rounds `stack up docs` must refuse, until the record names
    "starting". After that the checkout is the old release, which has no
    `stack rollback`: the admin guide's way out is `stack up` of each
    stacklet.
    """
    report.section("5. Kills before and after the checkout, back to the old release")
    before = documents_once_up() or []
    rounds, refusals = [], []
    seen_record, seen = open_record()
    while True:
        killed, record, done, code = rollback_until_a_new_step(seen_record, seen)
        new = done if record != seen_record else done[len(seen):]
        rounds.append([str(len(rounds) + 1), ", ".join(new) or "(record written)",
                       "killed" if killed else f"ended, exit {code}", ""])
        seen_record, seen = record, done
        if not killed or "finished" in done or "starting" in done:
            break
        up = stack(report, "up", "docs", tail=3)
        refusals.append(up.code != 0)
        rounds[-1][3] = f"`stack up docs` exit {up.code}"
    report.table(["round", "steps it did", "end", "between"], rounds)
    report.verdict("5", bool(refusals) and all(refusals) and "starting" in seen,
                   f"{len(rounds)} rounds until the record named {seen}; "
                   f"`stack up docs` refused {sum(refusals)} of {len(refusals)} times in between")
    stack(report, "version")
    # A kill at "starting" leaves the rollback's own `stack up` running:
    # it was started before the kill and is not in the killed group's
    # wait. Its run locks refuse a second start (exit 75), so the guide's
    # `stack up` runs once they are free.
    wait_for("the rollback's own start to end",
             lambda: not [f for f in audit.stale(DATA, CHECKOUT / ".stack") if "run lock" in f.why])
    up = stack(report, "up", *STACKLETS, timeout=2400)
    after = documents_once_up() or []
    report.verdict("6 after the checkout", up.code == 0 and len(after) == len(before),
                   f"the old release's `stack up` exit {up.code}; the page lists {len(after)} of {len(before)}")
    stale_after(report, "the kills and the old release's start", open_rollback_expected=True)


def rollback_until_a_new_step(record: Path | None, seen: list[str], checkout: Path = CHECKOUT,
                              read=open_record) -> tuple[bool, Path | None, list[str], int]:
    """Run `stack rollback --yes` and kill it once its record gained a step; (killed, record, done, exit code).

    `read` returns the newest record and its steps (`open_record`). The
    rollback runs in a session of its own, so no signal to the drill
    reaches it. When the poll ends in anything but the rollback's own
    end (a SIGTERM, an error of `read`), its whole process group is
    killed before the error goes on: a rollback left running would write
    into the folders that the cleanup deletes.
    """
    process = subprocess.Popen(["./stack", "rollback", "--yes"], cwd=checkout, start_new_session=True,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        while process.poll() is None:
            now, done = read()
            if (now != record and now is not None) or len(done) > len(seen):
                _kill_group(process)
                return True, now, read()[1], -9
            time.sleep(POLL_S)
    finally:
        if process.poll() is None:
            _kill_group(process)
    now, done = read()
    return False, now, done, process.returncode


def _kill_group(process: subprocess.Popen) -> None:
    """SIGKILL the process's group (its session, as `start_new_session` made it), then reap it."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def finish_on_the_build(report: Report) -> None:
    """Update to the build, follow the refusal of `stack rollback` back to the old release, finish, update again.

    The rollback of step 5 stopped in its start, and the old release
    started its stacklets without knowing the record. On the build,
    `stack rollback` refuses, because the checkout is no longer on the
    snapshot's release, and names the command that goes back to it. The
    admin guide's row "the checkout moved to another release" says: run
    that command, then `stack rollback`. Without this step the broken
    release's rollback would only finish the old one.
    """
    report.section("6. Back on the build, the open record finished")
    stack(report, "down", "all", timeout=900)
    stack(report, "update", releases.BUILD, "--yes")
    stack(report, "snapshot", "list")
    refused = stack(report, "rollback", "--yes", timeout=600, tail=10)
    back = f"stack update {releases.OLD}"
    stack(report, "update", releases.OLD, "--yes")
    finish = stack(report, "rollback", "--yes", timeout=2400, tail=20)
    _, done = open_record()
    report.verdict("6 after a moved checkout", refused.code != 0 and back in refused.out
                   and finish.code == 0 and "finished" in done,
                   f"`stack rollback` on the build: exit {refused.code}, names `{back}`: {back in refused.out}; "
                   f"after it: exit {finish.code}; the record names {done}")
    if "finished" not in done:
        end_the_record_by_hand(report)
    stack(report, "down", "all", timeout=900)
    stack(report, "update", releases.BUILD, "--yes")
    names = snapshot_names()
    free_before = audit.free_kb(DATA)
    restart = stack(report, "restart", timeout=2400)
    measure(report, "restart after the open record", new_snapshot(names), free_before, audit.free_kb(DATA),
            restart.seconds)
    stack(report, "version")
    stale_after(report, "finishing the open record on the build")


def end_the_record_by_hand(report: Report) -> None:
    """End the open record as the refusal for a gone snapshot says, so the steps after 6 can run.

    A snapshot's release without `stack rollback` cannot finish the
    record that a release with it sends the admin back to. The one exit
    the stack names is the one for a gone snapshot: on the snapshot's
    release, rename the record, then start the stacklets. The report
    marks it, because it is not the way out that the refusal names.
    """
    record, _ = open_record()
    if record is None:
        return
    report.line(f"Workaround: the way out the refusal names fails on {releases.OLD}; "
                f"the record is renamed to {record.name}.ended, as for a gone snapshot")
    report.run(["mv", str(record), f"{record}.ended"])
    stack(report, "up", *STACKLETS, timeout=2400)


def update_to_broken(report: Report, before: list[dict]) -> dict:
    """Update to the broken build; `stack up` refuses; the restart takes the snapshot and breaks the page."""
    report.section("7. Update to the broken build, restart")
    stack(report, "update", releases.BROKEN, "--yes")
    up = stack(report, "up", "docs")
    report.verdict("7", up.code != 0 and "stack restart docs" in up.out,
                   f"`stack up docs` after the update: exit {up.code}, names `stack restart docs`")
    images_before = container_images()
    names = snapshot_names()
    free_before = audit.free_kb(DATA)
    since = time.time()
    restart = stack(report, "restart", timeout=2400)
    free_after = audit.free_kb(DATA)
    snapshot = new_snapshot(names)
    gap = stop_and_start_gap(since)
    stack(report, "snapshot", "list")
    manifest = json.loads((SNAPSHOTS / snapshot / "MANIFEST.json").read_text()) if snapshot else {}
    in_it = sorted(p.name for p in (SNAPSHOTS / snapshot).iterdir() if p.is_dir()) if snapshot else []
    documents = documents_once_up()
    # The broken release changes only docs, so a bare restart restarts
    # docs alone, and its snapshot holds docs and the stack's own files.
    report.verdict("1", bool(snapshot) and "docs" in in_it and documents == [],
                   f"restart exit {restart.code} in {restart.seconds:.0f} s; snapshot {snapshot} holds {in_it}; "
                   f"the docs page answers and lists {documents} of {len(before)} documents")
    report.line(f"Stop to start of stack-docs-db during the restart: {gap}")
    measure(report, "at the restart", snapshot, free_before, free_after, restart.seconds)
    stale_after(report, "the restart's snapshot")
    return {"snapshot": snapshot, "manifest": manifest, "images_before": images_before}


def broken_release(report: Report, letters: Path) -> tuple[list[dict], dict, dict]:
    """Steps 7 and 8: the update to the broken release, then a late letter and a moved tag; what step 9 checks."""
    filed = documents_once_up() or []
    update = update_to_broken(report, filed)
    late = late_letter_and_moved_tag(report, letters)
    return filed, late, update


def late_letter_and_moved_tag(report: Report, letters: Path) -> dict:
    """One letter filed after the snapshot; the valkey tag moved to another image, as a pull moves it."""
    report.section("8. A letter after the snapshot, a moved image tag")
    files_before = set(originals())
    file_letter(report, letters / LETTER_AFTER)
    wait_for("the late letter's file", lambda: set(originals()) - files_before)
    late = sorted(set(originals()) - files_before)
    report.line(f"New files in the archive folder: {late}")
    was = image_id(MOVED_TAG)
    report.run(["docker", "pull", "-q", MOVED_TO], cwd=HOME)
    report.run(["docker", "image", "tag", MOVED_TO, MOVED_TAG], cwd=HOME)
    report.line(f"`{MOVED_TAG}`: {was} before, {image_id(MOVED_TAG)} after the move by hand")
    return {"late": late, "tag_was": was}


def rollback(report: Report, before: list[dict], late: dict, update: dict) -> None:
    """`stack rollback --yes`, then each promise of the rollback checked against the disk and the page."""
    report.section("9. The rollback to the build")
    done = stack(report, "rollback", "--yes", timeout=2400, tail=40)
    documents = documents_once_up() or []
    version = stack(report, "version")
    replaced = replaced_names()
    report.verdict("2", sorted(d["title"] for d in documents) == sorted(d["title"] for d in before)
                   and releases.BUILD.lstrip("v") in version.out and bool(replaced),
                   f"rollback exit {done.code} in {done.seconds:.0f} s; the page lists {documents}; "
                   f"version {version.out.strip()}; replaced {replaced}")
    still = [name for name in late["late"] if name in originals()]
    # The rollback lists each late file by its path under the data folder.
    paths = [line.strip() for line in done.out.splitlines() if line.strip().startswith("docs/")]
    named = [name for name in late["late"] if any(path.endswith(f"/{name}") for path in paths)]
    counted = [line.strip() for line in done.out.splitlines() if "came after the snapshot" in line][:1]
    report.verdict("3", bool(late["late"]) and still == late["late"] and named == late["late"],
                   f"late files {late['late']}: still on disk {still}, named by the rollback {named}; "
                   f"the rollback's warning: {counted or 'none'}")
    tag_now = image_id(MOVED_TAG)
    redis_after_rollback = image_id("stack-docs-redis", container=True)
    stack(report, "up", "docs")
    redis_after_up = image_id("stack-docs-redis", container=True)
    report.verdict("8", tag_now == late["tag_was"] and redis_after_up == late["tag_was"],
                   f"`{MOVED_TAG}`: {late['tag_was']} before the move, {tag_now} after the rollback; "
                   f"stack-docs-redis runs {redis_after_rollback}, after `stack up docs` {redis_after_up}")
    report.line(f"Container images before the update: {update['images_before']}")
    report.line(f"Container images now: {container_images()}")
    stack(report, "update", "--dry-run")
    stack(report, "doctor", tail=20)
    stale_after(report, "the rollback")


def large_snapshot(report: Report) -> None:
    """The cost of a large stacklet: 2 GB of random bytes in docs' own folder, then one snapshot."""
    report.section("10. The cost of 2 GB")
    report.run(["dd", "if=/dev/urandom", f"of={BIG_FILE}", "bs=1m", "count=2048"], cwd=HOME)
    names = snapshot_names()
    free_before = audit.free_kb(DATA)
    done = stack(report, "snapshot", "create", "docs", timeout=2400)
    free_after = audit.free_kb(DATA)
    taken = new_snapshot(names) if done.code == 0 else ""
    measure(report, "2 GB in docs", taken, free_before, free_after, done.seconds)
    BIG_FILE.unlink()
    stale_after(report, "`stack snapshot create docs` with 2 GB")


def kill_inside_the_checkout(report: Report, wrapper_dir: Path) -> None:
    """Update to the broken release again, restart, and stop git inside the rollback's checkout; the guide's steps."""
    report.section("11. A kill inside the checkout")
    before = documents_once_up() or []   # the build's page; the broken release's restart empties it
    stack(report, "update", releases.BROKEN, "--yes")
    stack(report, "snapshot", "list")
    stack(report, "doctor", tail=20)
    names = snapshot_names()
    free_before = audit.free_kb(DATA)
    restart = stack(report, "restart", timeout=2400)
    measure(report, "restart before the kill inside the checkout", new_snapshot(names), free_before,
            audit.free_kb(DATA), restart.seconds)
    newest = new_snapshot(names)
    manifest = json.loads((SNAPSHOTS / newest / "MANIFEST.json").read_text()) if newest else {}
    report.line(f"The newest snapshot {newest} names `applies` {manifest.get('applies')!r}")
    marker = wrapper_dir / "killed"
    env = {**os.environ, "PATH": f"{wrapper_dir}:{os.environ['PATH']}",
           "DRILL_REAL_GIT": shutil.which("git") or "/usr/bin/git", "DRILL_MARKER": str(marker)}
    for attempt in (1, 2):
        stack(report, "rollback", "--yes", env=env, timeout=2400, tail=20)
        if marker.exists():
            break
    report.line(f"The wrapper: {marker.read_text() if marker.exists() else 'never saw a checkout'}")
    locks = [name for name in ("HEAD.lock", "index.lock") if (CHECKOUT / ".git" / name).exists()]
    report.line(f"Locks left: {locks}")
    report.run(["git", "status", "--short"], tail=5)
    report.run(["git", "describe", "--tags"])
    again = stack(report, "rollback", "--yes", timeout=600, tail=10)
    # The admin guide's three steps, "After a stop inside the checkout", as written.
    report.run(["rm", "-f", ".git/HEAD.lock", ".git/index.lock"])
    report.run(["git", "checkout", "--force", "HEAD"])
    finish = stack(report, "rollback", "--yes", timeout=2400, tail=20)
    version = stack(report, "version")
    up = stack(report, "up", "docs")
    after = documents_once_up() or []
    report.verdict("6 inside the checkout", bool(locks) and finish.code == 0 and up.code == 0
                   and len(after) == len(before),
                   f"locks {locks}; `stack rollback` before the guide's steps exit {again.code}; after them "
                   f"exit {finish.code}; version {version.out.strip()}; `stack up docs` exit {up.code}; "
                   f"the page lists {len(after)} of {len(before)}")
    stale_after(report, "the rollback after the stop inside the checkout")


GIT_WRAPPER = '''#!/usr/bin/env python3
"""git, for the drill: a checkout is killed once git takes HEAD.lock, then the rollback with it."""
import os, signal, subprocess, sys, time
real, marker = os.environ["DRILL_REAL_GIT"], os.environ["DRILL_MARKER"]
args = sys.argv[1:]
if "checkout" not in args or os.path.exists(marker):
    os.execv(real, [real, *args])
root = args[args.index("-C") + 1] if "-C" in args else "."
lock = os.path.join(root, ".git", "HEAD.lock")
git = subprocess.Popen([real, *args])
hit = False
while git.poll() is None:
    if os.path.exists(lock):
        git.send_signal(signal.SIGKILL)
        hit = True
        break
git.wait()
with open(marker, "w") as out:
    out.write(f"git {' '.join(args)}: {'killed while it held HEAD.lock' if hit else 'ended before HEAD.lock was seen'}")
os.killpg(os.getpgid(os.getppid()), signal.SIGKILL)
'''


def remove_every_entry(report: Report) -> None:
    """`stack snapshot remove --yes` of each snapshot and replaced state; `.snapshots/` must end empty."""
    report.section("12. Remove every entry")
    if not (CHECKOUT / "lib/stack/snapshot").is_dir():   # the old release has no `stack snapshot`
        stack(report, "update", releases.BUILD, "--yes")
    for name in [*snapshot_names(), *(f"replaced/{n}" for n in replaced_names())]:
        stack(report, "snapshot", "remove", name, "--yes")
    left = sorted(str(p.relative_to(SNAPSHOTS)) for p in SNAPSHOTS.rglob("*")) if SNAPSHOTS.is_dir() else []
    report.verdict("9 remove", not left, f"left in .snapshots/ after removing every entry: {left[:20]}")
    stale_after(report, "the removes")


# ── The measures ─────────────────────────────────────────────────────────


SIZES: list[list[str]] = []
STALE: list[list[str]] = []


def measure(report: Report, when: str, snapshot: str, free_before: int, free_after: int, seconds: float,
            disk: str | None = None) -> None:
    """One snapshot's apparent size, files, the disk's loss and the size `stack snapshot list` prints.

    `disk` replaces the disk's loss with the reason it says nothing about
    the snapshot. An empty `snapshot` (the command took none) is a row too.
    """
    if not snapshot:
        SIZES.append([when, "no snapshot", "", "", "", "", ""])
        return
    size = audit.size(SNAPSHOTS / snapshot)
    listed = stack(report, "snapshot", "list", tail=6).out
    printed = next((line.strip() for line in listed.splitlines() if snapshot in line), "")
    SIZES.append([when, snapshot, f"{size.apparent_bytes / 1e6:.1f} MB", str(size.files),
                  disk or f"{(free_before - free_after) / 1024:.1f} MB", f"{seconds:.1f} s", printed])


# The kinds of finding of criterion 9, with the words the verdicts use.
STALE_KINDS = {
    "files": "stale files under .snapshots and .stack",
    "listed": "entries of .snapshots that `stack snapshot list` does not name",
    "tags": "image tags that name an image nobody runs or keeps",
    "doctor": "`stack doctor` lines about snapshots and rollbacks",
}
FAULTS: dict[str, list[str]] = {kind: [] for kind in STALE_KINDS}
IMAGES_BEFORE: list[str] = []   # the Mac's images before the drill: never a finding


def stale_after(report: Report, step: str, open_rollback_expected: bool = False) -> None:
    """The audit after one step: each kind of finding, which of them are faults, kept for the end.

    `open_rollback_expected` is for a step that kills a rollback on
    purpose: its open record is not a fault of that step.
    """
    has_snapshot = (CHECKOUT / "lib/stack/snapshot").is_dir()
    listed = stack(report, "snapshot", "list", tail=0).out if has_snapshot else ""
    doctor = stack(report, "doctor", tail=0).out
    found = {
        "files": [f"{f.path.name}: {f.why}" for f in audit.stale(DATA, CHECKOUT / ".stack")],
        # The old release has no `stack snapshot list` to compare with.
        "listed": audit.not_listed(SNAPSHOTS, listed) if has_snapshot else [],
        "tags": audit.stale_tags(inventory.take(RUN_FOLDER)["images"], stack_image_names(),
                                 container_image_ids(), kept_image_ids(), IMAGES_BEFORE),
        "doctor": [line.strip() for line in doctor.splitlines()
                   if "snapshot" in line.lower() or "rollback" in line.lower()],
    }
    faults = audit.faults(found, open_rollback_expected)
    for kind, texts in faults.items():
        FAULTS[kind] += [f"{step}: {text}" for text in texts]
    shown = {kind: "; ".join(texts) or "nothing" for kind, texts in found.items()}
    if not has_snapshot:
        shown["listed"] = "not checked: this release has no `stack snapshot list`"
    fault = "; ".join(f"{kind}: {len(texts)}" for kind, texts in faults.items() if texts) or "no"
    STALE.append([step, shown["files"], shown["listed"], shown["tags"], shown["doctor"], fault])
    report.line(f"Left after {step}: {shown}; faults: {fault}")


def judge_stale(report: Report) -> None:
    """One verdict of criterion 9 per kind of finding: it holds when no step left a fault of that kind."""
    for kind, words in STALE_KINDS.items():
        found = FAULTS[kind]
        report.verdict(f"9 {kind}", not found, f"{words}: " + ("; ".join(found[:5]) or "no step left one"))


def stack_image_names() -> set[str]:
    """The image names that the stack's containers start and that the snapshots' manifests name."""
    out = subprocess.run(["docker", "ps", "-a", "--filter", "name=stack-", "--format", "{{.Image}}"],
                         capture_output=True, text=True).stdout.split()
    return set(out) | {image["name"] for image in manifest_images() if "name" in image}


def container_image_ids() -> set[str]:
    """The image id of each `stack-*` container, running or stopped."""
    names = subprocess.run(["docker", "ps", "-a", "--filter", "name=stack-", "--format", "{{.Names}}"],
                           capture_output=True, text=True).stdout.split()
    return {image_id(name, container=True) for name in names} - {""}


def kept_image_ids() -> set[str]:
    """The image ids that a snapshot's or a replaced state's manifest keeps."""
    return {image["id"] for image in manifest_images() if "id" in image}


def manifest_images() -> list[dict]:
    """Every image entry of every manifest under `.snapshots/`; a manifest that does not read adds none."""
    entries = []
    if not SNAPSHOTS.is_dir():
        return entries
    for path in [*SNAPSHOTS.glob("*/MANIFEST.json"), *SNAPSHOTS.glob("replaced/*/MANIFEST.json")]:
        try:
            stacklets = json.loads(path.read_text()).get("stacklets", {})
        except (OSError, ValueError):
            continue
        for record in stacklets.values():
            entries += list(record.get("images", {}).values())
    return entries


# ── The cleanup ──────────────────────────────────────────────────────────


def the_mac(report: Report) -> cleaning.Mac:
    """The drill's Mac, for `drill_cleanup`: the report's `run` and `line`, and the drill's folders."""
    return cleaning.Mac(run=report.run, note=report.line, home=HOME, checkout=CHECKOUT,
                        drill_folder=DRILL_FOLDER, data=DATA, run_folder=RUN_FOLDER)


def cleanup(report: Report, before: dict, compared: dict) -> None:
    """Take the drill's instance away (`drill_cleanup.clean`); then the inventory after and criterion 4.

    `before` is this run's inventory, which the cleanup restores;
    `compared` the one criterion 4 compares with (`--baseline`, or
    `before`). A failed cleanup step fails criterion 4 too.
    """
    report.section("13. Cleanup and the inventory after")
    failures = cleaning.clean(the_mac(report), before)
    after: dict = {}
    failures += cleaning.run_all([("the inventory after", lambda: after.update(inventory.take(RUN_FOLDER)))],
                                 report.line)
    report.run(["docker", "system", "df"], cwd=HOME, tail=6)
    differences = inventory.differences(compared, after) if after else ["no inventory after"]
    if after:
        report.save("inventory-after.json", after)
    report.verdict("4", not differences and not failures,
                   f"differences of the two inventories: {differences or 'none'}; "
                   f"cleanup steps that failed: {failures or 'none'}")


# ── The run ──────────────────────────────────────────────────────────────


def hold(report: Report, wanted: bool) -> None:
    """With `--hold`, keep the instance up after the filing until a SIGTERM, which then cleans up.

    A SIGTERM before this point goes straight to the cleanup.
    """
    if wanted:
        report.line("Holding the instance for a look; `kill -TERM` the drill to clean up.")
        signal.pause()


def _stop(signum, frame) -> None:
    """Turn a stop signal into an exception, so `drill_cleanup.guarded` cleans up."""
    raise KeyboardInterrupt(f"signal {signum}")


def stop_on_signals() -> None:
    """SIGTERM, and SIGHUP unless it is ignored, end the run through the cleanup.

    A job started with `&` ignores SIGINT, so `kill -TERM` is how a
    person stops the drill. SIGHUP comes when the ssh session of the
    drill ends. Under `nohup` it is ignored, and stays ignored, because
    the run is meant to outlive the session then.
    """
    signal.signal(signal.SIGTERM, _stop)
    if signal.getsignal(signal.SIGHUP) is not signal.SIG_IGN:
        signal.signal(signal.SIGHUP, _stop)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--bundle", type=Path, required=True, help="git bundle of the build, with the old tag")
    parser.add_argument("--letters", type=Path, required=True, help="folder with the three letters")
    parser.add_argument("--report", type=Path, required=True, help="folder for the report, outside ~/famstack-dev")
    parser.add_argument("--ai-url", help="an AI server for the archivist (`stack ai switch`); none by default")
    parser.add_argument("--baseline", type=Path,
                        help="the inventory before an earlier, stopped attempt; criterion 4 compares with it")
    parser.add_argument("--hold", action="store_true",
                        help="after the filing, keep the instance up until SIGTERM, for a look by hand")
    parser.add_argument("--only-until", type=int, default=12,
                        help="the number of the last section to run, as the report numbers it (4 to 12)")
    args = parser.parse_args()
    report = Report(args.report)
    ai_key = sys.stdin.readline().strip() if args.ai_url else None
    before = preflight(report)   # refuses, and changes nothing, when another instance is here
    IMAGES_BEFORE[:] = before["images"]
    compared = before
    stop_on_signals()

    def body() -> None:
        nonlocal compared
        cleaning.keep(the_mac(report), before)
        if args.baseline:
            compared = json.loads(args.baseline.read_text())
            report.line(f"Criterion 4 compares with the inventory of an earlier attempt: {args.baseline}")
        wrapper_dir = args.report / "bin"
        wrapper_dir.mkdir(exist_ok=True)
        (wrapper_dir / "git").write_text(GIT_WRAPPER)
        (wrapper_dir / "git").chmod(0o755)
        clone(report, args.bundle)
        install(report, args.ai_url, ai_key)
        try:
            filed = file_letters_before(report, args.letters)
        except RuntimeError:
            hold(report, args.hold)
            raise
        hold(report, args.hold)
        steps = [
            (4, lambda: update_from_old(report, filed)),
            (5, lambda: kill_at_each_step(report)),
            (6, lambda: finish_on_the_build(report)),
            (9, lambda: rollback(report, *broken_release(report, args.letters))),   # sections 7 to 9
            (10, lambda: large_snapshot(report)),
            (11, lambda: kill_inside_the_checkout(report, wrapper_dir)),
            (12, lambda: remove_every_entry(report)),
        ]
        for number, step in steps:
            if number > args.only_until:
                break
            step()

    cleaning.guarded(body, lambda: cleanup(report, before, compared), report.line, traceback.format_exc)
    judge_stale(report)
    report.section("Snapshot sizes")
    report.table(["when", "snapshot", "apparent (du -A)", "files", "disk lost (df -k)", "time",
                  "`stack snapshot list`"], SIZES)
    report.section("What each step left")
    report.table(["after", "stale files", "not in `stack snapshot list`", "image tags", "doctor", "faults"], STALE)
    report.section("Verdicts")
    report.table(["criterion", "result", "evidence"],
                 [[c, "holds" if ok else "FAILS", text] for c, ok, text in report.verdicts])


if __name__ == "__main__":
    main()
