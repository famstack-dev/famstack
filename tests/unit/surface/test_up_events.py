"""`stack up <id> --events`: a run reported while it happens, one JSON object per line.

A screen that starts a stacklet (the dashboard's setup widget, through the
host API) follows the run step by step instead of waiting for its end. What
it reads is the envelope of ADR-020: `id` counting up from 1, `type`, `time`
in UTC and `data`; every step the terminal shows; a slow step as a start and
then its end; and one `result` last, carrying what `stack up` returns.

Driven through the real entry point, `stack.cli.main` with the command line
in `sys.argv`, from a checkout in `tmp_path` that holds one fabricated
stacklet. Only Docker is stood in for, as in test_lifecycle_sequencing.py.
Output is captured at the file descriptors (`capfd`), because that is where
`--events` keeps stdout for itself: the tools a run starts write to the same
descriptor, and the events have to stay the only thing on it.
"""

from __future__ import annotations

import json
import sys
import textwrap
import tomllib
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from stack import cli

INSTALL_STEPS = textwrap.dedent("""
    def run(ctx):
        ctx.step("Creating the app's folders")
        ctx.step("Seeding the app")
""")

DECLINE = textwrap.dedent("""
    from stack.hooks import Cancelled
    def run(ctx):
        raise Cancelled("Cancelled. Nothing changed.")
""")

BREAK = textwrap.dedent("""
    def run(ctx):
        raise RuntimeError("the app's port is taken")
""")


@pytest.fixture(autouse=True)
def no_docker():
    with patch("stack.docker.ensure_network", return_value=("mocked", None)), \
         patch("stack.docker.compose_pull", return_value=None), \
         patch("stack.docker.compose_up", return_value=(0, "")):
        yield


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A checkout with one stacklet, `app`, that has never been up.

    It has a compose file, so the run reaches the Docker phase and its
    spinners, and a generated secret in its env and in a hint, as `ai`
    names the oMLX admin password in one, so the test can tell whether
    either left for a screen. Returns a function that adds a hook.
    """
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\ntimezone = "Europe/Berlin"\n')
    (tmp_path / ".stack").mkdir()
    (tmp_path / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')
    app = tmp_path / "stacklets" / "app"
    (app / "hooks").mkdir(parents=True)
    (app / "stacklet.toml").write_text(textwrap.dedent("""
        id = "app"
        name = "App"
        hints = ["Admin UI at http://localhost:42999 (password: {app__APP_SECRET})"]
        [env]
        generate = ["APP_SECRET"]
        [env.defaults]
        APP_SECRET = "{app__APP_SECRET}"
    """))
    (app / "docker-compose.yml").write_text("services:\n  app:\n    image: busybox\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STACK_DIR", raising=False)
    # A notification goes to the server room through `./stack messages`,
    # which this checkout does not have.
    monkeypatch.setattr(cli, "_notify", lambda _stck, _msg: None)

    def hook(name: str, code: str) -> None:
        (app / "hooks" / f"{name}.py").write_text(code)

    return hook


def up(capfd, monkeypatch, *argv: str) -> tuple[int, str, str]:
    """Run `stack up <argv>` as the shell does: (exit code, stdout, stderr)."""
    monkeypatch.setattr(sys, "argv", ["stack", "up", *argv])
    try:
        cli.main()
        code = 0
    except SystemExit as exit_:
        code = exit_.code or 0
    out, err = capfd.readouterr()
    return code, out, err


def events(stdout: str) -> list[dict]:
    """The event lines on stdout. Fails on any line that is not one."""
    return [json.loads(line) for line in stdout.splitlines()]


def seen(evs: list[dict]) -> list[tuple[str, str | None]]:
    """Each event as (type, text), the way a screen lists the run."""
    return [(e["type"], e["data"].get("text")) for e in evs]


# ── A run that works ─────────────────────────────────────────────────────

def test_each_step_arrives_as_an_event_and_the_result_comes_last(checkout, capfd, monkeypatch):
    """The hook's own steps are among the run's steps, in the order they
    happened, and the run ends with exactly one result."""
    checkout("on_install", INSTALL_STEPS)

    code, out, _ = up(capfd, monkeypatch, "app", "--events")

    evs = events(out)
    assert code == 0
    steps = seen(evs)
    assert steps.index(("step.done", "Creating the app's folders")) \
        < steps.index(("step.done", "Seeding the app"))
    assert [e["type"] for e in evs].count("result") == 1
    assert evs[-1]["type"] == "result"
    assert evs[-1]["data"]["ok"] is True
    assert evs[-1]["data"]["stacklet"] == "app"


def test_every_event_carries_the_envelope(checkout, capfd, monkeypatch):
    """`id` counts up from 1 within the run, so a screen can order events
    and resume after the last it saw; `time` is UTC, whatever the Mac's
    time zone."""
    _, out, _ = up(capfd, monkeypatch, "app", "--events")

    evs = events(out)
    assert [set(e) for e in evs] == [{"id", "type", "time", "data"}] * len(evs)
    assert [e["id"] for e in evs] == list(range(1, len(evs) + 1))
    assert all(datetime.fromisoformat(e["time"]).utcoffset() == timedelta(0) for e in evs)


def test_a_slow_step_starts_and_then_ends(checkout, capfd, monkeypatch):
    """What the terminal shows as a spinner is a start, then its end, which
    names the event that started it so a screen can replace that row."""
    _, out, _ = up(capfd, monkeypatch, "app", "--events")

    evs = events(out)
    started = next(e for e in evs if seen([e]) == [("step.started", "Pulling images")])
    ended = [e for e in evs if e["data"].get("started") == started["id"]]
    assert seen(ended) == [("step.done", "Pulling images")]
    assert ended[0]["id"] > started["id"]


def test_the_terminal_still_gets_its_output_on_stderr(checkout, capfd, monkeypatch):
    """Whoever runs it in a terminal sees what they saw before, and the
    welcome screen does not land between the events."""
    checkout("on_install", INSTALL_STEPS)

    _, _, err = up(capfd, monkeypatch, "app", "--events")

    assert "Creating the app's folders" in err
    assert "App is running" in err


def test_the_stacklets_secrets_stay_off_the_stream(checkout, capfd, monkeypatch, tmp_path):
    """The result `stack up` returns holds the rendered env and the
    manifest's hints, and both can carry a password. These lines go to
    screens, so no line holds it; the terminal still shows the hint."""
    _, out, err = up(capfd, monkeypatch, "app", "--events")

    secret = tomllib.loads((tmp_path / ".stack" / "secrets.toml").read_text())["app__APP_SECRET"]
    assert events(out)[-1]["data"]["ok"] is True
    assert [line for line in out.splitlines() if secret in line] == []
    assert secret in err


# ── A run that does not ──────────────────────────────────────────────────

def test_a_hook_that_cancels_ends_the_stream_with_the_cancellation(checkout, capfd, monkeypatch):
    checkout("on_start", DECLINE)

    code, out, _ = up(capfd, monkeypatch, "app", "--events")

    evs = events(out)
    assert code == 1
    assert evs[-1]["type"] == "result"
    assert evs[-1]["data"] == {"cancelled": "Cancelled. Nothing changed."}


def test_a_stacklet_that_fails_to_start_ends_the_stream_with_its_error(checkout, capfd, monkeypatch):
    checkout("on_start", BREAK)

    code, out, _ = up(capfd, monkeypatch, "app", "--events")

    evs = events(out)
    assert code == 1
    assert ("step.failed", "on_start hook failed") in seen(evs)
    assert evs[-1]["type"] == "result"
    assert evs[-1]["data"]["error"] == "on_start hook failed"


# ── Without --events ─────────────────────────────────────────────────────

def test_without_events_stack_up_prints_what_it_always_printed(checkout, capfd, monkeypatch):
    """The resting state: no event lines, the welcome screen on stdout."""
    code, out, err = up(capfd, monkeypatch, "app")

    assert code == 0
    assert "App is running" in out
    assert '"type"' not in out + err
