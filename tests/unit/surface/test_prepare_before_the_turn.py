"""A stacklet's long setup runs outside the stack's queue: `on_prepare`.

One run of the stack at a time (`stack.runs`) keeps the end of a first
start, which recreates core, from breaking another run. A setup step that
needs neither Docker nor core (a Homebrew install, a model download) has
no reason to hold every other start for half an hour. A stacklet puts such
a step into its `on_prepare` hook. The framework runs it holding only that
stacklet's run lock:

- `stack <id> prepare` runs it on its own;
- `stack up <id>` runs it after its setup questions and before its turn in
  the queue, so a start whose preparation was done takes its turn at once.

The preparing run is a real `stack engine prepare` or `stack up engine --events`
in a process of its own, held inside its hook until the test writes a line
to its stdin, as another surface would start it. Docker is a stand-in that
answers every command with success. The command that must not wait is
`stack.cli.main` in the test.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from stack import cli
from stack.runs import EXIT_ALREADY_RUNNING

REPO = Path(__file__).resolve().parents[3]

# Holds only in a process the test started with HOLD_PREPARE set. It
# reports a step, a slow step with its percent, then waits for a line.
HOLD_WHILE_PREPARING = textwrap.dedent("""
    import os, sys
    def run(ctx):
        if os.environ.get("HOLD_PREPARE"):
            with ctx.stack.output.spinner("Downloading the model") as download:
                download.progress(42)
                ctx.step("Holding the preparation")
                sys.stdin.readline()
        prepared = ctx.stack.data / "engine"
        prepared.mkdir(parents=True, exist_ok=True)
        (prepared / "prepared").write_text("the model")
""")

FAILING_PREPARATION = textwrap.dedent("""
    def run(ctx):
        raise RuntimeError("Not enough free space: the model needs 6.2 GB")
""")


@pytest.fixture(autouse=True)
def no_docker():
    with patch("stack.docker.ensure_network", return_value=("mocked", None)), \
         patch("stack.docker.compose_pull", return_value=None), \
         patch("stack.docker.compose_up", return_value=(0, "")), \
         patch("stack.docker.compose_stop", return_value=(0, "")), \
         patch("stack.docker.compose_down", return_value=(0, "")), \
         patch("stack.docker.all_project_ids", return_value=["engine"]):
        yield


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A checkout with two stacklets that were never up: `engine`, which has
    an `on_prepare` hook that can be held open, and `other`, which has
    none. Returns its directory."""
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\ntimezone = "Europe/Berlin"\n')
    (tmp_path / ".stack").mkdir()
    (tmp_path / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')
    for sid in ("engine", "other"):
        stacklet = tmp_path / "stacklets" / sid
        (stacklet / "hooks").mkdir(parents=True)
        (stacklet / "stacklet.toml").write_text(f'id = "{sid}"\nname = "{sid.title()}"\n')
        (stacklet / "docker-compose.yml").write_text(f"services:\n  {sid}:\n    image: busybox\n")
    (tmp_path / "stacklets" / "engine" / "hooks" / "on_prepare.py").write_text(HOLD_WHILE_PREPARING)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "docker").chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STACK_DIR", raising=False)
    monkeypatch.delenv("STACK_RUN_SURFACE", raising=False)
    monkeypatch.delenv("STACK_RUN_QUEUE", raising=False)
    # A notification goes to the server room through `./stack messages`,
    # which this checkout does not have.
    monkeypatch.setattr(cli, "_notify", lambda _stck, _msg: None)
    return tmp_path


class Preparing:
    """`stack <argv>` in its own process, held inside engine's on_prepare
    until `finish`. Its events are read when the command has `--events`."""

    def __init__(self, root: Path, *argv: str):
        env = {**os.environ, "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
               "PYTHONPATH": str(REPO / "lib"), "HOLD_PREPARE": "1"}
        env.pop("STACK_DIR", None)
        env.pop("STACK_RUN_SURFACE", None)
        self.log = root / ".stack" / "runs" / "engine.jsonl"
        self.stderr = root / "preparing.stderr"
        self.process = subprocess.Popen(
            [sys.executable, "-m", "stack", *argv], cwd=root, env=env, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr.open("w"))

    def __enter__(self) -> Preparing:
        """Wait until the hook holds: its step is in engine's run log."""
        import time
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if any(e["data"].get("text") == "Holding the preparation" for e in self.events()):
                return self
            assert self.process.poll() is None, self.stderr.read_text()
            time.sleep(0.02)
        raise AssertionError(f"the preparation never held: {self.stderr.read_text()}")

    def events(self) -> list[dict]:
        """What engine's run log holds so far."""
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]

    def finish(self) -> tuple[int, str]:
        """Let the hook return: (exit code, stdout) once the command ended."""
        out, _ = self.process.communicate("\n", timeout=60)
        return self.process.returncode, out

    def __exit__(self, *_exc) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=60)


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


# ── `stack <id> prepare` ─────────────────────────────────────────────────

def test_another_stacklet_starts_while_one_prepares(checkout, capfd, monkeypatch):
    """Given `stack engine prepare` runs, when `stack up other` starts, then
    it runs to its end at once instead of waiting in the queue: a
    preparation holds only its own stacklet's lock."""
    with Preparing(checkout, "engine", "prepare") as preparing:
        code, _, err = stack(capfd, monkeypatch, "up", "other")

        assert code == 0, err
        assert preparing.process.poll() is None
        assert preparing.finish()[0] == 0
    assert (checkout / "data" / "engine" / "prepared").exists()


def test_a_start_of_the_stacklet_that_prepares_is_refused(checkout, capfd, monkeypatch):
    """Given `stack engine prepare` runs, when `stack up engine` starts, then it
    is refused as a second run of engine, with the busy exit code."""
    with Preparing(checkout, "engine", "prepare") as preparing:
        code, _, err = stack(capfd, monkeypatch, "up", "engine")

        assert code == EXIT_ALREADY_RUNNING
        assert "engine" in err
        assert preparing.finish()[0] == 0


def test_a_second_preparation_is_refused(checkout, capfd, monkeypatch):
    """Given `stack engine prepare` runs, a second one does not run twice."""
    with Preparing(checkout, "engine", "prepare") as preparing:
        code, _, _ = stack(capfd, monkeypatch, "engine", "prepare")

        assert code == EXIT_ALREADY_RUNNING
        assert preparing.finish()[0] == 0


def test_a_preparation_reports_its_steps_and_percent_in_the_run_log(checkout):
    """The run log, which the dashboard and the host API follow, shows the
    slow step as started with its percent, then done."""
    with Preparing(checkout, "engine", "prepare") as preparing:
        code, _ = preparing.finish()
    events = preparing.events()

    assert code == 0
    started = next(e for e in events if e["type"] == "step.started")
    progress = next(e for e in events if e["type"] == "step.progress")
    assert progress["data"]["percent"] == 42
    assert progress["data"]["started"] == started["id"]
    assert any(e["type"] == "step.done" and e["data"].get("started") == started["id"]
               for e in events)
    assert events[-1]["type"] == "result" and events[-1]["data"]["ok"] is True


def test_a_failed_preparation_fails_the_command_with_its_reason(checkout, capfd, monkeypatch):
    (checkout / "stacklets" / "engine" / "hooks" / "on_prepare.py").write_text(FAILING_PREPARATION)

    code, _, err = stack(capfd, monkeypatch, "engine", "prepare")

    assert code == 1
    assert "6.2 GB" in err


def test_a_stacklet_without_a_preparation_has_no_prepare_command(checkout, capfd, monkeypatch):
    """`prepare` is there for a stacklet with an `on_prepare` hook only."""
    code, _, _ = stack(capfd, monkeypatch, "other", "prepare")

    assert code != 0
    assert not (checkout / ".stack" / "runs" / "other.jsonl").exists()


@pytest.mark.usefixtures("docker_with_no_containers")
@pytest.mark.parametrize("argv", [["down", "all"], ["uninstall", "--yes"]],
                         ids=["down all", "uninstall"])
def test_a_command_for_every_stacklet_is_refused_while_one_prepares(
        checkout, capfd, monkeypatch, argv):
    """Given `stack engine prepare` runs, when `stack down all` or `stack
    uninstall` starts, then it is refused before it changes anything,
    naming the preparation: it would stop or delete what is being set up.
    `archive`, set up and listed before engine, keeps its data."""
    archive = checkout / "stacklets" / "archive"
    archive.mkdir()
    (archive / "stacklet.toml").write_text('id = "archive"\nname = "Archive"\n')
    (archive / "docker-compose.yml").write_text("services:\n  archive:\n    image: busybox\n")
    (checkout / "data" / "archive").mkdir(parents=True)
    with Preparing(checkout, "engine", "prepare") as preparing:
        code, _, err = stack(capfd, monkeypatch, *argv)

        assert code == EXIT_ALREADY_RUNNING, err
        assert "engine" in err and "prepare" in err
        assert (checkout / "stack.toml").exists()
        assert (checkout / "data" / "archive").exists()
        assert preparing.finish()[0] == 0


def test_an_uninstall_that_keeps_the_preparing_stacklet_is_refused_too(
        checkout, capfd, monkeypatch):
    """Given `stack engine prepare` runs, when `stack uninstall --not
    engine` starts, then it is refused all the same: it deletes stack.toml
    and the instance's state, which the preparation reads and writes."""
    with Preparing(checkout, "engine", "prepare") as preparing:
        code, _, err = stack(capfd, monkeypatch, "uninstall", "--yes", "--not", "engine")

        assert code == EXIT_ALREADY_RUNNING, err
        assert "engine" in err and "prepar" in err
        assert (checkout / "stack.toml").exists()
        assert preparing.finish()[0] == 0


@pytest.mark.usefixtures("docker_with_no_containers")
@pytest.mark.parametrize("argv", [["down", "all"], ["restart", "all"]],
                         ids=["down all", "restart all"])
def test_a_command_for_all_leaves_the_run_log_of_a_stacklet_it_does_not_touch(
        checkout, capfd, monkeypatch, argv):
    """Given `other` is not set up and its last run failed, when `stack
    down all` or `stack restart all` runs, then other's run log still
    holds that failure: a screen still shows why it did not start."""
    log = checkout / ".stack" / "runs" / "other.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    failed = ('{"id": 0, "type": "run", "data": {"stacklet": "other", "action": "up"}}\n'
              '{"id": 1, "type": "result", "data": {"error": "image pull failed"}}\n')
    log.write_text(failed)

    code, _, err = stack(capfd, monkeypatch, *argv)

    assert code == 0, err
    assert log.read_text() == failed


# ── `stack up` prepares before its turn ──────────────────────────────────

def test_a_start_prepares_before_its_turn(checkout, capfd, monkeypatch):
    """Given `stack up engine` is preparing, when `stack up other` starts,
    then it runs at once: the start of engine has not taken its turn yet.
    Then engine's start ends with its preparation done."""
    with Preparing(checkout, "up", "engine", "--events") as preparing:
        code, _, err = stack(capfd, monkeypatch, "up", "other")

        assert code == 0, err
        assert preparing.process.poll() is None
        code, out = preparing.finish()
    events = [json.loads(line) for line in out.splitlines()]

    assert code == 0
    assert events[-1]["type"] == "result" and events[-1]["data"]["ok"] is True
    assert any(e["type"] == "step.progress" for e in events)
    assert (checkout / "data" / "engine" / "prepared").exists()


def test_a_failed_preparation_ends_the_start_before_its_turn(checkout, capfd, monkeypatch):
    """A preparation that fails ends `stack up` with its reason, and the
    stacklet's run log records the failed result for the screens."""
    (checkout / "stacklets" / "engine" / "hooks" / "on_prepare.py").write_text(FAILING_PREPARATION)

    code, _, err = stack(capfd, monkeypatch, "up", "engine")

    assert code == 1
    assert "6.2 GB" in err
    log = checkout / ".stack" / "runs" / "engine.jsonl"
    result = [json.loads(line) for line in log.read_text().splitlines()][-1]
    assert result["type"] == "result" and "error" in result["data"]
    assert not (checkout / ".stack" / "engine.setup-done").exists()
