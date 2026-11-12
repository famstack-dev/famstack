"""One run per stacklet: a second `stack up` of a stacklet that is starting is refused.

The terminal, the host API (the Server room, the dashboard) and the menu bar
app all start a stacklet with `stack up`, each in a process of its own. Two
first runs of one stacklet would both run its setup hooks, so the second is
refused with its own exit code and says who runs the first. A run of another
stacklet waits its turn and says so: one run of the stack at a time. When the
first run ends, in any way, the next one runs.

The first run is a real `stack up app --events` in a process of its own, as
it is when another surface starts it. Its on_install hook reports a step and
then waits for a line on its stdin, so the test knows the run holds its lock
(the step arrives on stdout) and decides when it ends (a line, or a kill).
A stand-in `docker` first on that process's `PATH` answers every command
with success. A refused second command is `stack.cli.main` in the test,
with Docker stood in as in test_up_events.py; a queued one is a process of
its own, read through its run log.
"""

from __future__ import annotations

import json
import os
import pty
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from stack import cli
from stack.runs import EXIT_ALREADY_RUNNING

REPO = Path(__file__).resolve().parents[3]

# Waits only in the first run's process, which the test starts with
# HOLD_RUN set; a later run of the same first setup goes straight through.
HOLD_UNTIL_TOLD = textwrap.dedent("""
    import os, sys
    def run(ctx):
        if os.environ.get("HOLD_RUN"):
            ctx.step("Holding the run")
            sys.stdin.readline()
        written = ctx.stack.data / "app"
        written.mkdir(parents=True, exist_ok=True)
        (written / "written-by-the-install").write_text("the family's new data")
""")


@pytest.fixture(autouse=True)
def no_docker():
    with patch("stack.docker.ensure_network", return_value=("mocked", None)), \
         patch("stack.docker.compose_pull", return_value=None), \
         patch("stack.docker.compose_up", return_value=(0, "")), \
         patch("stack.docker.compose_stop", return_value=(0, "")), \
         patch("stack.docker.compose_down", return_value=(0, "")), \
         patch("stack.docker.all_project_ids", return_value=["app"]):
        yield


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A checkout with two stacklets that were never up: `app`, whose
    first run can be held open, and `other`. Returns its directory."""
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\ntimezone = "Europe/Berlin"\n')
    (tmp_path / ".stack").mkdir()
    (tmp_path / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')
    for sid in ("app", "other"):
        stacklet = tmp_path / "stacklets" / sid
        (stacklet / "hooks").mkdir(parents=True)
        (stacklet / "stacklet.toml").write_text(f'id = "{sid}"\nname = "{sid.title()}"\n')
        (stacklet / "docker-compose.yml").write_text(f"services:\n  {sid}:\n    image: busybox\n")
    (tmp_path / "stacklets" / "app" / "hooks" / "on_install.py").write_text(HOLD_UNTIL_TOLD)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STACK_DIR", raising=False)
    monkeypatch.delenv("STACK_RUN_SURFACE", raising=False)
    # A notification goes to the server room through `./stack messages`,
    # which this checkout does not have.
    monkeypatch.setattr(cli, "_notify", lambda _stck, _msg: None)
    return tmp_path


class FirstRun:
    """`stack up <stacklet> --events` in its own process, held inside
    on_install. The stacklet's on_install is HOLD_UNTIL_TOLD."""

    def __init__(self, root: Path, surface: str | None = None, stacklet: str = "app"):
        bin_dir = root / "bin"
        bin_dir.mkdir(exist_ok=True)
        docker = bin_dir / "docker"
        docker.write_text("#!/bin/sh\nexit 0\n")
        docker.chmod(0o755)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
               "PYTHONPATH": str(REPO / "lib"), "HOLD_RUN": "1"}
        env.pop("STACK_DIR", None)
        env.pop("STACK_RUN_SURFACE", None)
        if surface:
            env["STACK_RUN_SURFACE"] = surface
        self.stderr = root / "first-run.stderr"
        self.process = subprocess.Popen(
            [sys.executable, "-m", "stack", "up", stacklet, "--events"],
            cwd=root, env=env, text=True, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.stderr.open("w"))
        self.events: list[dict] = []

    def __enter__(self) -> FirstRun:
        """Wait until the run holds its lock: its hook's step arrived."""
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.events.append(json.loads(line))
            if self.events[-1]["data"].get("text") == "Holding the run":
                return self
        raise AssertionError(f"the first run ended early: {self.stderr.read_text()}")

    def finish(self) -> int:
        """Let the hook return; the run's exit code once it ended."""
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write("\n")
        self.process.stdin.close()
        self.events += [json.loads(line) for line in self.process.stdout]
        return self.process.wait(timeout=60)

    def kill(self) -> None:
        """End the run as a crash does: SIGKILL, no cleanup of its own."""
        self.process.send_signal(signal.SIGKILL)
        self.process.wait(timeout=60)

    def __exit__(self, *_exc) -> None:
        if self.process.poll() is None:
            self.kill()


def stack(capfd, monkeypatch, *argv: str) -> tuple[int, str, str]:
    """Run `stack <argv>` as the shell does: (exit code, stdout, stderr)."""
    monkeypatch.setattr(sys, "argv", ["stack", *argv])
    try:
        cli.main()
        code = 0
    except SystemExit as exit_:
        code = exit_.code or 0
    out, err = capfd.readouterr()
    return code, out, err


# ── A second run of the same stacklet ─────────────────────────────────────

def test_a_second_start_is_refused_while_the_first_completes(checkout, capfd, monkeypatch):
    """Given `stack up app` runs, when a second `stack up app` starts, then
    it does not run: it exits with its own code and names the stacklet and
    where the first run came from. The first run then completes."""
    with FirstRun(checkout) as first:
        code, _, err = stack(capfd, monkeypatch, "up", "app")

        assert code == EXIT_ALREADY_RUNNING
        assert "app" in err and "terminal" in err
        assert first.finish() == 0
    assert first.events[-1]["type"] == "result"
    assert first.events[-1]["data"]["ok"] is True


def test_the_refusal_names_the_surface_and_the_run(checkout, capfd, monkeypatch):
    """With `--events` the refusal is one `result` carrying who runs the
    stacklet: the surface the first run's caller named, its start, its pid."""
    with FirstRun(checkout, surface="dashboard") as first:
        code, out, _ = stack(capfd, monkeypatch, "up", "app", "--events")

        result = [json.loads(line) for line in out.splitlines()][-1]
        assert code == EXIT_ALREADY_RUNNING
        assert result["type"] == "result"
        running = result["data"]["running"]
        assert running["surface"] == "dashboard"
        assert running["pid"] == first.process.pid
        assert running["started"]
        assert first.finish() == 0


@pytest.mark.parametrize("argv", [["down", "app"], ["restart", "app"],
                                  ["destroy", "app", "--yes"]])
def test_stop_restart_and_destroy_are_refused_the_same_way(checkout, capfd, monkeypatch, argv):
    """Given `stack up app` runs, `down`, `restart` and `destroy` of app
    are refused with the same exit code, and the start goes on."""
    with FirstRun(checkout) as first:
        code, _, _ = stack(capfd, monkeypatch, *argv)

        assert code == EXIT_ALREADY_RUNNING
        assert first.finish() == 0


# ── Another stacklet waits its turn ───────────────────────────────────────

class Queued:
    """`stack <argv>` in its own process, started while the first run holds
    the stack's lock; read through other's run log, as the dashboard's
    tile reads it."""

    def __init__(self, root: Path, argv: list[str]):
        env = {**os.environ, "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
               "PYTHONPATH": str(REPO / "lib")}
        env.pop("STACK_DIR", None)
        env.pop("STACK_RUN_SURFACE", None)
        env.pop("HOLD_RUN", None)
        self.log = root / ".stack" / "runs" / "other.jsonl"
        self.process = subprocess.Popen(
            [sys.executable, "-m", "stack", *argv], cwd=root, env=env, text=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def waiting_step(self) -> dict:
        """The first step other's run log shows, once it shows one."""
        while True:
            assert self.process.poll() is None, self.process.communicate()[0]
            events = [json.loads(line) for line in self.log.read_text().splitlines()] \
                if self.log.exists() else []
            steps = [e for e in events if e["type"].startswith("step")]
            if steps:
                return {"run": events[0], "step": steps[0]}
            time.sleep(0.02)

    def ended(self) -> int:
        out, _ = self.process.communicate(timeout=60)
        assert self.process.returncode == 0, out
        return self.process.returncode


@pytest.mark.parametrize("argv", [["up", "other", "--events"], ["restart", "other"]],
                         ids=["start, as the dashboard and the Server room run it", "restart"])
def test_another_stacklet_waits_its_turn_and_says_so(checkout, argv):
    """Given `stack up app` runs, when another stacklet's start or restart
    begins, then that run has begun (its run log is open, so a tile and the
    host API see it) and its first step says it waits for app's run; it
    does nothing else until app's run ended, then runs to its end.

    One run at a time: the end of a first start recreates core, and a run
    that used core meanwhile failed."""
    with FirstRun(checkout) as first:
        other = Queued(checkout, argv)
        seen = other.waiting_step()

        assert seen["run"]["data"]["stacklet"] == "other"
        assert seen["step"]["type"] == "step.started"
        assert "app" in seen["step"]["data"]["text"]
        assert other.process.poll() is None
        assert first.finish() == 0
    assert other.ended() == 0


def test_after_a_killed_run_the_next_start_runs(checkout, capfd, monkeypatch):
    """Given the first run is killed, then the next start runs: the kernel
    released the lock with the process, nothing stale is left."""
    with FirstRun(checkout) as first:
        first.kill()

    code, _, _ = stack(capfd, monkeypatch, "up", "app")

    assert code == 0


def test_a_single_start_holds_no_lock_afterwards(checkout, capfd, monkeypatch):
    """Resting state: after a run, the lock is free for any process."""
    import fcntl
    code, _, _ = stack(capfd, monkeypatch, "up", "app")

    lock = checkout / ".stack" / "runs" / "app.lock"
    with open(lock) as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert code == 0


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def test_an_update_waits_until_the_run_ended(checkout):
    """Given `stack up app` runs, when `stack update` moves to a release,
    then it says it waits for app's run and leaves the checkout where it is
    until that run ended; then it moves. A start reads the code it started
    from to its end."""
    _git(checkout.parent, "init", "-q", str(checkout))
    (checkout / ".gitignore").write_text("*\n!VERSION\n!.gitignore\n")
    for tag in ("v0.0.1", "v0.0.2"):
        (checkout / "VERSION").write_text(tag + "\n")
        _git(checkout, "add", "-A")
        _git(checkout, "-c", "user.name=t", "-c", "user.email=t@example.com",
             "commit", "-qm", f"release {tag}")
        _git(checkout, "tag", tag)
    _git(checkout, "checkout", "-q", "v0.0.1")

    with FirstRun(checkout) as first:
        update = Queued(checkout, ["update", "v0.0.2", "--yes"])
        assert update.process.stdout is not None
        said = ""
        while "app" not in said or "Waiting" not in said:
            line = update.process.stdout.readline()
            assert line, said
            said = line if "Waiting" in line else said

        assert _git(checkout, "describe", "--tags") == "v0.0.1"
        assert first.finish() == 0
    assert update.ended() == 0
    assert _git(checkout, "describe", "--tags") == "v0.0.2"


def test_a_destroy_never_removes_what_a_start_meanwhile_set_up(checkout):
    """Given app is not set up and only a failed setup's data is left, and
    `stack up other` sets app up as other's requirement, when `stack
    destroy app --yes` comes meanwhile, then destroy deletes nothing: app
    ends up set up, with the data its install wrote.

    The stack deletes nothing of the family's implicitly: a destroy that
    decided "only leftover data" before it ran must not act on that once a
    start set the stacklet up."""
    (checkout / "stacklets" / "other" / "stacklet.toml").write_text(
        'id = "other"\nname = "Other"\nrequires = ["app"]\n')
    leftover = checkout / "data" / "app"
    leftover.mkdir(parents=True)
    (leftover / "from-a-failed-setup").write_text("leftover")

    with FirstRun(checkout, stacklet="other") as first:
        destroy = Queued(checkout, ["destroy", "app", "--yes"])
        assert destroy.process.stdout is not None
        while destroy.process.poll() is None:
            line = destroy.process.stdout.readline()
            if not line or "Waiting" in line:
                break
        assert first.finish() == 0
    destroy.process.communicate(timeout=60)

    assert (checkout / ".stack" / "app.setup-done").exists()
    assert (leftover / "written-by-the-install").exists()


ASKS_IN_THE_TERMINAL = textwrap.dedent("""
    def run(ctx):
        print("Your server's name? ", flush=True)
        input()
""")


def test_a_setup_question_waiting_in_a_terminal_holds_up_no_other_run(checkout):
    """Given `stack up app` in a terminal waits at app's setup question,
    when the dashboard starts other, then other runs to its end at once;
    app's start goes on once the person answered.

    A question can wait for hours, so a first start asks before it takes
    its turn in the queue of runs."""
    (checkout / "stacklets" / "app" / "hooks" / "on_configure.py").write_text(
        ASKS_IN_THE_TERMINAL)
    person, terminal = pty.openpty()
    env = {**os.environ, "PATH": f"{checkout / 'bin'}:{os.environ['PATH']}",
           "PYTHONPATH": str(REPO / "lib")}
    for name in ("STACK_DIR", "STACK_RUN_SURFACE", "HOLD_RUN"):
        env.pop(name, None)
    (checkout / "bin").mkdir(exist_ok=True)
    (checkout / "bin" / "docker").write_text("#!/bin/sh\nexit 0\n")
    (checkout / "bin" / "docker").chmod(0o755)
    first = subprocess.Popen([sys.executable, "-m", "stack", "up", "app"], cwd=checkout,
                             env=env, text=True, stdin=terminal, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
    try:
        assert first.stdout is not None
        said = ""
        while "Your server" not in said:
            line = first.stdout.readline()
            assert line, said
            said += line

        other = Queued(checkout, ["up", "other", "--events"])
        assert other.ended() == 0
        assert first.poll() is None

        os.write(person, b"simpson\n")
        assert first.wait(timeout=60) == 0
    finally:
        if first.poll() is None:
            first.kill()
        os.close(person)
        os.close(terminal)


def _doctor(root: Path) -> list[dict]:
    """`stack doctor --json` in a process of its own, with the stand-in
    `docker` first on PATH and this checkout as HOME, so it reads no
    extension of the machine running the suite: its findings."""
    env = {**os.environ, "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
           "PYTHONPATH": str(REPO / "lib"), "HOME": str(root)}
    for name in ("STACK_DIR", "STACK_RUN_SURFACE", "HOLD_RUN"):
        env.pop(name, None)
    done = subprocess.run([sys.executable, "-m", "stack", "doctor", "--json"], cwd=root,
                          env=env, text=True, capture_output=True, timeout=60)
    return json.loads(done.stdout)["findings"]


def test_doctor_names_the_run_every_other_run_waits_for(checkout):
    """Given `stack up app` runs, when the admin asks `stack doctor`, then
    one finding names that run, its surface and its process, because every
    other run waits for it; with no run going on there is no such finding."""
    with FirstRun(checkout, surface="dashboard") as first:
        going_on = [f for f in _doctor(checkout) if "up app" in f["title"]]
        assert first.finish() == 0

    assert len(going_on) == 1
    assert "dashboard" in going_on[0]["title"]
    assert str(first.process.pid) in going_on[0]["title"]
    assert not [f for f in _doctor(checkout) if "up app" in f["title"]]
