"""Bots wait for the migrations of the data they act on.

`stack up` and `stack restart` start every stacklet first and run their
migrations after, because a migration may need a service another
stacklet starts (Forgejo, a command in the bot runner). For that stretch
the new code is up and the data still has its old shape. The bot runner
is one of the stacklets that starts, and it holds its bots until no
enabled stacklet has a migration left to run. A message that arrives
meanwhile stays on the room's timeline and is handled once the bot runs.
With nothing pending, nothing waits and nothing more is said.

The CLI runs for real (`handle_up`, `handle_restart`) over fabricated
stacklets:

- `core`, whose `docker compose up` starts the real bot runner
  (stacklets/core/bot-runner/main.py) in a process of its own, as the
  container does;
- `docs`, whose archivist writes to a log what it did, and handles every
  message on its timeline, a file standing in for the Matrix room;
- `memory`, which ships one migration.

Docker is a stand-in, and so is Matrix: the runner is told the
homeserver answers, and the bot reads its room from a file.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = REPO_ROOT / "stacklets" / "core" / "bot-runner"
sys.path.insert(0, str(REPO_ROOT / "lib"))

MARGE = "Marge: the dentist moved to Tuesday"

# The runner as the container starts it (`python -u main.py` in /app, the
# framework at /app/stack), pointed at the fabricated instance. Matrix is
# the one edge replaced: the homeserver answers at once.
RUNNER_DRIVER = textwrap.dedent("""
    import asyncio, sys
    from pathlib import Path

    runner, lib, stacklets, state, data = sys.argv[1:6]
    sys.path[:0] = [runner, lib]
    import main

    main.SETUP_STATE_DIR = Path(state)
    main.DATA_DIR = Path(data)
    main.STACKLET_ROOTS = (Path(stacklets),)
    main.MIGRATIONS_POLL_SECS = 0.05

    async def matrix_answers(timeout=None):
        return True

    main.wait_for_matrix = matrix_answers
    asyncio.run(main.main())
""")

ARCHIVIST = '''
from pathlib import Path


class ArchivistBot:
    """Says in the log that it runs, then handles its room's timeline."""

    def __init__(self, *, log, timeline, **_):
        self.name = "archivist-bot"
        self._log, self._timeline = Path(log), Path(timeline)

    async def start(self):
        room = self._timeline.read_text().splitlines() if self._timeline.exists() else []
        with self._log.open("a") as f:
            f.write("archivist acts\\n")
            for message in room:
                f.write(f"archivist handles: {message}\\n")

    async def stop(self):
        pass
'''


def _migration(log: Path, *, fail: bool) -> str:
    raises = "    raise RuntimeError('the code host did not answer')\n" if fail else ""
    return ('"""Give every note its sources."""\n'
            "from pathlib import Path\n"
            "def run(ctx):\n" + raises +
            f"    with Path({str(log)!r}).open('a') as f:\n"
            "        f.write('memory migrated\\n')\n")


class Instance:
    """An installed instance being updated: core, docs and memory set up,
    memory shipping a migration its data may or may not have had."""

    def __init__(self, root: Path, *, migrated: bool = False, failing: bool = False):
        self.root = root
        self.log = root / "log"
        self.timeline = root / "timeline"
        self.runner_log = root / "runner.log"
        self.runner: subprocess.Popen | None = None
        self.marge_writes_meanwhile = False

        state = root / ".stack"
        state.mkdir()
        (root / "stack.toml").write_text('[core]\ntimezone = "Europe/Berlin"\n')
        (state / "secrets.toml").write_text(
            'global__ADMIN_PASSWORD = "test"\n'
            'docs__ARCHIVIST_BOT_PASSWORD = "test"\n')
        for sid in ("core", "docs", "memory"):
            sdir = root / "stacklets" / sid
            sdir.mkdir(parents=True)
            (sdir / "stacklet.toml").write_text(f'id = "{sid}"\nname = "{sid}"\n')
            (state / f"{sid}.setup-done").touch()
        (root / "stacklets" / "core" / "docker-compose.yml").write_text("services: {}\n")
        bot = root / "stacklets" / "docs" / "bot"
        bot.mkdir()
        (bot / "bot.toml").write_text(
            'id = "archivist-bot"\nname = "Archivist"\n\n[settings]\n'
            f'log = "{self.log}"\ntimeline = "{self.timeline}"\n')
        (bot / "archivist.py").write_text(ARCHIVIST)
        self.write_migration(failing=failing)
        if migrated:
            record = root / "data" / "memory" / ".migrations.json"
            record.parent.mkdir(parents=True)
            record.write_text('{"applied": {"0001": {"name": "give_notes_their_sources"}},'
                              ' "failed": {}}\n')

        from stack import Stack
        from stack.output import CollectorOutput
        self.stack = Stack(root=root, data=root / "data", output=CollectorOutput())

    def write_migration(self, *, failing: bool) -> None:
        migrations = self.root / "stacklets" / "memory" / "migrations"
        migrations.mkdir(exist_ok=True)
        (migrations / "0001_give_notes_their_sources.py").write_text(
            _migration(self.log, fail=failing))

    # ── Docker, as far as these tests need it ────────────────────────────

    def compose_up(self, compose_file, env=None):
        """Starting core starts the bot runner. Returns once the runner has
        either launched its bots or said it holds them, so what follows in
        the CLI happens after that decision."""
        if Path(compose_file).parent.name == "core":
            self._start_runner()
            if self.marge_writes_meanwhile:
                with self.timeline.open("a") as f:
                    f.write(MARGE + "\n")
        return 0, ""

    def _start_runner(self) -> None:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
               "HOME": str(self.root), "MATRIX_SERVER_NAME": "springfield.example",
               "MATRIX_HOMESERVER": "http://127.0.0.1:9"}
        self.runner = subprocess.Popen(
            [sys.executable, "-I", "-c", RUNNER_DRIVER, str(RUNNER), str(REPO_ROOT / "lib"),
             str(self.root / "stacklets"), str(self.root / ".stack"), str(self.root / "data")],
            stdout=subprocess.DEVNULL, stderr=self.runner_log.open("w"), env=env,
            cwd=self.root)
        deadline = time.monotonic() + 15
        while not any(w in self.said() for w in ("Launching", "Holding")):
            assert self.runner.poll() is None, self.said()
            assert time.monotonic() < deadline, self.said()
            time.sleep(0.02)

    def said(self) -> str:
        """What the bot runner logged, as `docker logs` shows it."""
        return self.runner_log.read_text() if self.runner_log.exists() else ""

    def runner_finishes(self) -> None:
        """The fabricated bot returns once it has acted, and the runner with it."""
        assert self.runner is not None
        self.runner.wait(timeout=15)

    def stop_runner(self) -> None:
        if self.runner is not None and self.runner.poll() is None:
            self.runner.kill()
            self.runner.wait()

    # ── What happened ────────────────────────────────────────────────────

    def happened(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def told(self) -> list[str]:
        out = self.stack.output
        return out.steps + out.warnings + out.errors


@pytest.fixture
def make_instance(tmp_path):
    made: list[Instance] = []

    def make(**kwargs) -> Instance:
        instance = Instance(tmp_path, **kwargs)
        made.append(instance)
        return instance

    yield make
    for instance in made:
        instance.stop_runner()


@pytest.fixture
def docker(make_instance):
    """Patches Docker for whichever instance the test makes."""
    holder: dict[str, Instance] = {}

    def compose_up(compose_file, env=None):
        return holder["instance"].compose_up(compose_file, env)

    with patch("stack.docker.ensure_network", return_value=("mocked", None)), \
         patch("stack.docker.compose_pull", return_value=(0, "")), \
         patch("stack.docker.compose_up", side_effect=compose_up), \
         patch("stack.docker.compose_stop", return_value=(0, "")), \
         patch("stack.docker.compose_down", return_value=(0, "")):
        yield holder


IDS = ["core", "docs", "memory"]


def _up(instance: Instance) -> None:
    """`stack up core docs memory`."""
    from stack.cli import handle_up
    handle_up(instance.stack, argparse.Namespace(stacklet=list(IDS), no_voice=False))


def _restart(instance: Instance) -> None:
    """`stack restart core docs memory`, which `stack restart` runs after an update."""
    from stack.cli import handle_restart
    handle_restart(instance.stack, argparse.Namespace(stacklet=list(IDS), yes=True))


COMMANDS = [pytest.param(_up, id="up"), pytest.param(_restart, id="restart")]


@pytest.mark.parametrize("command", COMMANDS)
def test_a_bot_does_not_act_until_the_migration_has_run(command, make_instance, docker):
    """Given memory ships a migration its data has not had, when the stack
    comes up, then the archivist starts only after the migration ran, and
    Marge's message from the meantime is handled then, not lost.

    The archivist is the docs stacklet's bot, run by core, and neither
    declares that it needs memory: core starts before memory does.
    """
    instance = docker["instance"] = make_instance()
    instance.marge_writes_meanwhile = True

    command(instance)
    instance.runner_finishes()

    assert instance.happened() == [
        "memory migrated", "archivist acts", f"archivist handles: {MARGE}"]
    assert "Bots wait until these migrations have run: memory/0001" in instance.told()
    assert "memory/0001" in instance.said()


@pytest.mark.parametrize("command", COMMANDS)
def test_with_nothing_pending_nothing_waits_and_nothing_more_is_said(
        command, make_instance, docker):
    instance = docker["instance"] = make_instance(migrated=True)

    command(instance)
    instance.runner_finishes()

    assert instance.happened() == ["archivist acts"]
    assert "Holding" not in instance.said()
    assert not [line for line in instance.told() if "wait" in line or "igrat" in line]


def test_a_failed_migration_keeps_the_bots_held_and_says_what_failed(
        make_instance, docker, capsys):
    """Given the migration fails, then the bots stay held, the output names
    the failure and the stacklet, and the next `stack up memory` that gets
    it through lets them go."""
    instance = docker["instance"] = make_instance(failing=True)

    with pytest.raises(SystemExit):
        _restart(instance)
    time.sleep(0.3)  # six of the runner's checks

    assert instance.runner is not None and instance.runner.poll() is None
    assert instance.happened() == []
    told = instance.told()
    assert "Migration memory/0001 failed: the code host did not answer" in told
    assert "Bots keep waiting until these migrations have run: memory/0001" in told
    assert "memory" in capsys.readouterr().err

    instance.write_migration(failing=False)
    from stack.cli import CLI
    assert CLI(instance.stack).up("memory")["ok"]
    instance.runner_finishes()

    assert instance.happened() == ["memory migrated", "archivist acts"]
