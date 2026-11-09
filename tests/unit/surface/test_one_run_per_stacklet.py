"""One run per stacklet: a second `stack up` of a stacklet that is starting is refused.

The terminal, the host API (the Server room, the dashboard) and the menu bar
app all start a stacklet with `stack up`, each in a process of its own. Two
first runs of one stacklet would both run its setup hooks, so the second is
refused with its own exit code and says who runs the first. A run of another
stacklet goes on. When the first run ends, in any way, the next one runs.

The first run is a real `stack up app --events` in a process of its own, as
it is when another surface starts it. Its on_install hook reports a step and
then waits for a line on its stdin, so the test knows the run holds its lock
(the step arrives on stdout) and decides when it ends (a line, or a kill).
No sleeps. A stand-in `docker` first on that process's `PATH` answers every
command with success. The second command is `stack.cli.main` in the test,
with Docker stood in as in test_up_events.py.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
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


# ── Everything else runs ──────────────────────────────────────────────────

def test_another_stacklet_starts_meanwhile(checkout, capfd, monkeypatch):
    """A run of app holds only app's lock: `stack up other` runs."""
    with FirstRun(checkout) as first:
        code, _, _ = stack(capfd, monkeypatch, "up", "other")

        assert code == 0
        assert first.finish() == 0


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
