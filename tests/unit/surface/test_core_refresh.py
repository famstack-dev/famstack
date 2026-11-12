"""Core is recreated after a start only when its rendered env changed.

Core's env reads secrets that other stacklets write on their first run
(an API token, a bot's password). At the end of `stack up <id>` the stack
renders core's env again, and recreates core's containers when that env
is different from what core runs, so the bot runner gets the new value.
When nothing core reads changed, core keeps running: the dashboard stays
connected and the bots are not restarted.

Driven through the real entry point, `stack.cli.main` with the command line
in `sys.argv`, from a checkout in `tmp_path` with two fabricated stacklets:
`core`, whose env reads `app__APP_TOKEN`, and `app`, whose first run writes
that token. Docker is stood in for in process, as in test_up_events.py:
`compose_up` records the compose file it was given, and core counts as
running, with containers that carry no commit stamp unless a test
gives them one.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from stack import cli

WRITE_TOKEN = textwrap.dedent("""
    def run(ctx):
        ctx.secret("APP_TOKEN", "token-from-the-first-run")
""")


@pytest.fixture
def compose_ups():
    """Docker stood in for; returns the stacklet directories `compose up`
    ran against, in order. Core is running on this Docker."""
    started: list[str] = []

    def compose_up(compose_file, env=None):
        started.append(Path(compose_file).parent.name)
        return (0, "")

    with patch("stack.docker.ensure_network", return_value=("mocked", None)), \
         patch("stack.docker.compose_pull", return_value=None), \
         patch("stack.docker.compose_up", side_effect=compose_up), \
         patch("stack.docker.running_project_ids", return_value={"core", "app"}), \
         patch("stack.docker.container_commits", return_value={}):
        yield started


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A checkout with `core` already up and `app` never started.

    Core's env reads the token app writes in its first run, as the real
    core reads `docs__API_TOKEN`. Returns the checkout's directory.
    """
    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\ntimezone = "Europe/Berlin"\n')
    (tmp_path / ".stack").mkdir()
    (tmp_path / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')

    core = tmp_path / "stacklets" / "core"
    (core / "hooks").mkdir(parents=True)
    (core / "stacklet.toml").write_text(textwrap.dedent("""
        id = "core"
        name = "Core"
        [env.defaults]
        APP_TOKEN = "{app__APP_TOKEN}"
    """))
    (core / "docker-compose.yml").write_text("services:\n  core:\n    image: busybox\n")

    app = tmp_path / "stacklets" / "app"
    (app / "hooks").mkdir(parents=True)
    (app / "stacklet.toml").write_text('id = "app"\nname = "App"\n')
    (app / "docker-compose.yml").write_text("services:\n  app:\n    image: busybox\n")
    (app / "hooks" / "on_install.py").write_text(WRITE_TOKEN)

    # A stacklet with a bot and nothing core's env reads, as the agent:
    # its bot password goes to the secret store, which the bot runner
    # reads itself.
    helper = tmp_path / "stacklets" / "helper"
    (helper / "bot").mkdir(parents=True)
    (helper / "stacklet.toml").write_text('id = "helper"\nname = "Helper"\n')
    (helper / "docker-compose.yml").write_text("services:\n  helper:\n    image: busybox\n")
    (helper / "bot" / "bot.toml").write_text('id = "helper-bot"\nname = "Helper"\n')

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STACK_DIR", raising=False)
    # A notification goes to the server room through `./stack messages`,
    # which this checkout does not have.
    monkeypatch.setattr(cli, "_notify", lambda _stck, _msg: None)
    return tmp_path


def stack(*args):
    """`./stack <args>` through the real entry point; returns its exit code."""
    with patch.object(sys, "argv", ["stack", *args]):
        try:
            cli.main()
        except SystemExit as e:
            return e.code or 0
    return 0


def test_a_start_that_changes_nothing_core_reads_leaves_core_running(checkout, compose_ups):
    """Given core and app are up, and nothing core reads changed since,
    when the admin runs `stack up app` again,
    then compose recreates app and not core.

    Every recreate of core restarts the dashboard's server and the bot
    runner, so a start of any stacklet would drop the page's connection
    and restart every bot."""
    assert stack("up", "core") == 0
    assert stack("up", "app") == 0
    compose_ups.clear()

    assert stack("up", "app") == 0

    assert compose_ups == ["app"]


def test_a_first_start_that_writes_a_secret_core_reads_recreates_core(checkout, compose_ups):
    """Given core is up and app was never started,
    when `stack up app` runs app's first start, which writes a token core reads,
    then core is recreated after app, with the token in its env file.

    The bot runner reads the token from core's env, and compose does not
    recreate a container for a changed env file on its own."""
    assert stack("up", "core") == 0
    compose_ups.clear()

    assert stack("up", "app") == 0

    assert compose_ups == ["app", "core"]
    assert "token-from-the-first-run" in (checkout / "stacklets" / "core" / ".env").read_text()


def test_a_first_start_recreates_core_even_when_its_env_is_unchanged(checkout, compose_ups):
    """Given core is up and helper, a stacklet with a bot, was never started,
    when `stack up helper` runs helper's first start, which changes nothing
    in core's env,
    then core is recreated after helper; a second start leaves core running.

    The bot runner finds bots only when it starts, in the stacklets with a
    setup-done marker, which a first start writes."""
    assert stack("up", "core") == 0
    compose_ups.clear()
    env_before = (checkout / "stacklets" / "core" / ".env").read_text()

    assert stack("up", "helper") == 0

    assert compose_ups == ["helper", "core"]
    assert (checkout / "stacklets" / "core" / ".env").read_text() == env_before

    compose_ups.clear()
    assert stack("up", "helper") == 0
    assert compose_ups == ["helper"]


def test_a_first_start_among_several_recreates_core(checkout, compose_ups):
    """Given core is up and helper was never started,
    when `stack up app,helper` starts both, helper for the first time,
    then core is recreated once, after both."""
    assert stack("up", "core") == 0
    assert stack("up", "app") == 0
    compose_ups.clear()

    assert stack("up", "app,helper") == 0

    assert compose_ups == ["app", "helper", "core"]


def test_up_core_recreates_core_when_nothing_changed(checkout, compose_ups):
    """Given core is up and nothing it reads changed,
    when the admin runs `stack up core`,
    then core is recreated: `stack up` of a stacklet always applies it."""
    assert stack("up", "core") == 0
    compose_ups.clear()

    assert stack("up", "core") == 0

    assert compose_ups == ["core"]


def test_a_failed_recreate_of_core_is_retried_on_the_next_start(checkout, compose_ups):
    """Given core is up, and its recreate fails after app's first start,
    when the admin runs `stack up app` again,
    then core is recreated this time.

    Core still runs with the old env after a failed recreate, so the next
    start must see the new token as a change, not as what core runs."""
    assert stack("up", "core") == 0
    compose_ups.clear()

    def core_refuses(compose_file, env=None):
        name = Path(compose_file).parent.name
        compose_ups.append(name)
        return (1, "core refused") if name == "core" else (0, "")

    with patch("stack.docker.compose_up", side_effect=core_refuses):
        assert stack("up", "app") == 0
    compose_ups.clear()

    assert stack("up", "app") == 0

    assert compose_ups == ["app", "core"]


# ── After an update, before `stack restart` ──────────────────────────────

def git(root, *args):
    """A git command in the test checkout, with an identity and no hooks."""
    import subprocess
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=test@example.com",
         "-c", "user.name=Test", "-c", "core.hooksPath=/dev/null", *args],
        check=True, capture_output=True, text=True)


@pytest.fixture
def updated_checkout(checkout, compose_ups):
    """The checkout as a git tree whose core runs code it has moved past.

    Core's containers carry the commit they started from; a later commit
    changes core's compose, as an update does. Docker is stood in for as
    in `compose_ups`, which this stamp overrides. Returns the checkout."""
    git(checkout, "init", "-q")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "release one")
    import subprocess
    started_from = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    (checkout / "stacklets" / "core" / "docker-compose.yml").write_text(
        "services:\n  core:\n    image: busybox\n    ports: ['42070:8080']\n")
    git(checkout, "commit", "-q", "-am", "release two")
    with patch("stack.docker.container_commits", return_value={"core": started_from}):
        yield checkout


def test_a_start_before_the_restart_leaves_old_core_running(updated_checkout, compose_ups, capsys):
    """Given core runs code the checkout has moved past, after an update,
    when the admin runs `stack up app` before `stack restart`,
    then core is not recreated, and the terminal says to run `./stack restart`.

    Core's new compose can publish a port an old container of another
    stacklet still holds. A recreate of core alone then fails to bind, and
    the dashboard and the home address stay down. `stack restart` stops
    every stale stacklet before it starts any, so it applies core safely."""
    def new_core_cannot_bind(compose_file, env=None):
        name = Path(compose_file).parent.name
        compose_ups.append(name)
        if name == "core":
            return (1, "Bind for 0.0.0.0:42070 failed: port is already allocated")
        return (0, "")

    with patch("stack.docker.compose_up", side_effect=new_core_cannot_bind):
        assert stack("up", "app") == 0

    assert compose_ups == ["app"]
    assert "The update is not applied yet. Run: ./stack restart" in capsys.readouterr().err


def test_a_failed_recreate_of_core_is_shown_as_a_failure(checkout, compose_ups, capsys):
    """Given core is up on the current code, and its recreate fails,
    when `stack up app` runs app's first start,
    then the terminal shows the failure with its reason, marked ✗, not ✓."""
    assert stack("up", "core") == 0
    capsys.readouterr()

    def core_refuses(compose_file, env=None):
        name = Path(compose_file).parent.name
        return (1, "pull access denied") if name == "core" else (0, "")

    with patch("stack.docker.compose_up", side_effect=core_refuses):
        assert stack("up", "app") == 0

    err = capsys.readouterr().err
    line = next(ln for ln in err.splitlines() if "core refresh failed" in ln)
    assert "✗" in line and "✓" not in line
    assert "pull access denied" in line
