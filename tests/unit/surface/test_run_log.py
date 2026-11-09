"""The run log: every run of a stacklet leaves its events in `.stack/runs/<id>.jsonl`.

A run started from the terminal, the host API (the Server room, the
dashboard) or the menu bar app reports its steps to whoever started it. The
run log keeps the same events on the Mac, in ADR-020's envelope, so another
surface can follow any run: a `run` event first (who runs it, since when,
which process, which action), then the steps and warnings as they happen,
and one `result` last. A run that ends without one (Ctrl-C, a crash) is
over when its lock is free.

Driven through the real entry point, `stack.cli.main` with the command line
in `sys.argv`, from a checkout in `tmp_path` with two fabricated stacklets,
as in test_one_run_per_stacklet.py, whose held first run in a process of
its own this file reuses. Only Docker is stood in for.
"""

from __future__ import annotations

import json
import sys
import textwrap
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from stack import cli
from stack.runs import EXIT_ALREADY_RUNNING
from tests.unit.surface.test_one_run_per_stacklet import HOLD_UNTIL_TOLD, FirstRun

INSTALL_STEPS = textwrap.dedent("""
    def run(ctx):
        ctx.step("Creating the app's folders")
        ctx.warn("The app's port is close to another one")
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
    first run reports a step and a warning, and `other`, whose first run
    can be held open. `app` has a generated secret in its env and in a hint, as `ai`
    names the oMLX admin password in one. Returns its directory."""
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\ntimezone = "Europe/Berlin"\n')
    (tmp_path / ".stack").mkdir()
    (tmp_path / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')
    for sid in ("app", "other"):
        stacklet = tmp_path / "stacklets" / sid
        (stacklet / "hooks").mkdir(parents=True)
        (stacklet / "docker-compose.yml").write_text(f"services:\n  {sid}:\n    image: busybox\n")
    (tmp_path / "stacklets" / "app" / "stacklet.toml").write_text(textwrap.dedent("""
        id = "app"
        name = "App"
        hints = ["Admin UI at http://localhost:42999 (password: {app__APP_SECRET})"]
        [env]
        generate = ["APP_SECRET"]
        [env.defaults]
        APP_SECRET = "{app__APP_SECRET}"
    """))
    (tmp_path / "stacklets" / "other" / "stacklet.toml").write_text('id = "other"\nname = "Other"\n')
    (tmp_path / "stacklets" / "app" / "hooks" / "on_install.py").write_text(INSTALL_STEPS)
    (tmp_path / "stacklets" / "other" / "hooks" / "on_install.py").write_text(HOLD_UNTIL_TOLD)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STACK_DIR", raising=False)
    monkeypatch.delenv("STACK_RUN_SURFACE", raising=False)
    # A notification goes to the server room through `./stack messages`,
    # which this checkout does not have.
    monkeypatch.setattr(cli, "_notify", lambda _stck, _msg: None)
    return tmp_path


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


def run_log(root: Path, stacklet: str) -> list[dict]:
    """The events in a stacklet's run log, as a screen reads them."""
    path = root / ".stack" / "runs" / f"{stacklet}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def seen(evs: list[dict]) -> list[tuple[str, str | None]]:
    """Each event as (type, text), the way a screen lists the run."""
    return [(e["type"], e["data"].get("text")) for e in evs]


# ── A run from the terminal ──────────────────────────────────────────────

def test_a_start_from_the_terminal_leaves_its_run_in_the_log(checkout, capfd, monkeypatch):
    """Given `stack up app` in a terminal, then its run log holds the run:
    a `run` event first naming the terminal, the action and this process,
    the hook's step and warning in between, one `result` last."""
    code, out, err = stack(capfd, monkeypatch, "up", "app")

    evs = run_log(checkout, "app")
    assert code == 0
    assert evs[0]["type"] == "run"
    assert evs[0]["data"]["surface"] == "terminal"
    assert evs[0]["data"]["action"] == "up"
    assert evs[0]["data"]["started"]
    assert evs[0]["data"]["pid"]
    assert ("step.done", "Creating the app's folders") in seen(evs)
    assert ("warning", "The app's port is close to another one") in seen(evs)
    assert [e["type"] for e in evs].count("result") == 1
    assert evs[-1]["type"] == "result"
    assert evs[-1]["data"]["ok"] is True
    assert evs[-1]["data"]["stacklet"] == "app"
    assert [e["id"] for e in evs] == list(range(1, len(evs) + 1))
    # The terminal output stays as it was: the steps on stderr, the
    # summary on stdout, and no event among them.
    assert "Creating the app's folders" in err
    assert "App is running" in out
    assert '"type"' not in out + err


def test_the_log_holds_the_steps_that_events_shows(checkout, capfd, monkeypatch):
    """Given `stack up app --events`, then the log and stdout list the same
    steps; the log adds only its `run` event, and stdout keeps its own."""
    code, out, _ = stack(capfd, monkeypatch, "up", "app", "--events")

    on_stdout = [json.loads(line) for line in out.splitlines()]
    assert code == 0
    assert on_stdout[0]["type"] != "run"
    assert seen(run_log(checkout, "app"))[1:] == seen(on_stdout)


def test_the_surface_that_started_the_run_is_in_the_log(checkout, capfd, monkeypatch):
    """A caller that names its surface is named in the `run` event."""
    monkeypatch.setenv("STACK_RUN_SURFACE", "dashboard")

    stack(capfd, monkeypatch, "up", "app")

    assert run_log(checkout, "app")[0]["data"]["surface"] == "dashboard"


@pytest.mark.parametrize("argv, action", [(["down", "app"], "down"),
                                          (["restart", "app"], "restart"),
                                          (["destroy", "app", "--yes"], "destroy")])
def test_stop_restart_and_destroy_leave_their_run_too(checkout, capfd, monkeypatch, argv, action):
    """Given app ran, a `down`, `restart` or `destroy` of it replaces the
    log with its own run: one `run` event naming its action, one `result`."""
    stack(capfd, monkeypatch, "up", "app")

    code, _, _ = stack(capfd, monkeypatch, *argv)

    evs = run_log(checkout, "app")
    assert code == 0
    types = [e["type"] for e in evs]
    assert types.count("run") == 1 and types.count("result") == 1
    assert evs[0]["data"]["action"] == action
    assert evs[-1]["type"] == "result"


def test_a_new_run_replaces_the_last_one(checkout, capfd, monkeypatch):
    """The log holds the current or the last run, never two."""
    stack(capfd, monkeypatch, "up", "app")
    stack(capfd, monkeypatch, "up", "app")

    types = [e["type"] for e in run_log(checkout, "app")]
    assert types.count("run") == 1
    assert types[0] == "run" and types[-1] == "result"


def test_the_log_shows_no_secret(checkout, capfd, monkeypatch):
    """The result of `stack up` holds the rendered env and the hints, and
    both carry the app's password. The log goes to screens: it holds
    neither, while the terminal still shows the hint."""
    _, out, _ = stack(capfd, monkeypatch, "up", "app")

    secret = tomllib.loads((checkout / ".stack" / "secrets.toml").read_text())["app__APP_SECRET"]
    assert secret not in (checkout / ".stack" / "runs" / "app.jsonl").read_text()
    assert secret in out


# ── A run that is going on, or ended without its result ──────────────────

def test_the_log_fills_while_the_run_goes_on(checkout, capfd, monkeypatch):
    """Given a run that is still going on, its log already holds its
    `run` event and the steps so far, and no `result` yet."""
    with FirstRun(checkout, stacklet="other") as first:
        evs = run_log(checkout, "other")

        assert evs[0]["type"] == "run"
        assert evs[0]["data"]["pid"] == first.process.pid
        assert ("step.done", "Holding the run") in seen(evs)
        assert "result" not in [e["type"] for e in evs]
        assert first.finish() == 0
    assert run_log(checkout, "other")[-1]["type"] == "result"


def test_a_refused_run_leaves_the_log_to_the_running_one(checkout, capfd, monkeypatch):
    """Only the holder of the lock writes the log: a second start that is
    refused changes nothing in it."""
    with FirstRun(checkout, stacklet="other") as first:
        before = (checkout / ".stack" / "runs" / "other.jsonl").read_text()

        code, _, _ = stack(capfd, monkeypatch, "up", "other")

        assert code == EXIT_ALREADY_RUNNING
        assert (checkout / ".stack" / "runs" / "other.jsonl").read_text() == before
        assert first.finish() == 0


def test_a_killed_run_leaves_its_log_without_a_result(checkout, capfd, monkeypatch):
    """Given the run is killed, its log ends without `result`; the free
    lock tells a reader that the run is over."""
    with FirstRun(checkout, stacklet="other") as first:
        first.kill()

    types = [e["type"] for e in run_log(checkout, "other")]
    assert types[0] == "run"
    assert "result" not in types


# ── Resting state ────────────────────────────────────────────────────────

def test_a_stacklet_that_never_ran_has_no_log(checkout, capfd, monkeypatch):
    """Resting state: a run of app writes app's log only; `other`, which
    never ran, has none."""
    stack(capfd, monkeypatch, "up", "app")

    assert (checkout / ".stack" / "runs" / "app.jsonl").exists()
    assert not (checkout / ".stack" / "runs" / "other.jsonl").exists()
