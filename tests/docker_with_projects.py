"""`docker` as a Mac has it when the stack's stacklets run, for tests that start and stop them.

`docker_with_no_containers` answers for a Mac where nothing runs. A test for
`stack up`, `stack down` or `stack list` needs `docker` to say which
stacklets run, and to take `compose up`, `pull` and `down`. This stand-in
answers from what the test sets, and writes down every command it gets, so
a test asserts on what reached Docker instead of replacing the code that
sends it:

    def test_a_second_up_pulls_before_it_starts(docker_with_projects):
        docker = docker_with_projects()
        ...
        assert docker.compose_steps() == ["pull", "up"]

    def test_stack_list_shows_a_running_stacklet(docker_with_projects):
        docker_with_projects(core="running", docs="failing")

The states are the ones `stack list` shows: `running`, `starting`,
`failing`, `stopped`. They reach the code as the `Status` text of
`docker compose ls`, the way a real daemon words it.

    docker compose ls ...             one row per stacklet set
    docker compose up|pull|down ...   exit 0, or the failure set with `fail`
    docker exec <container> ...       exit 0 in a container of a stacklet that
                                      runs, or the failure set with `exec_fails`;
                                      "No such container" otherwise
    docker info                       exit 0
    docker network inspect|create     exit 0
    docker compose config             no services
    docker ps ...                     the commits set with `commits`
    docker inspect | exec | logs ...  "No such container", exit 1

Anything else exits non-zero and says so, as `docker_with_no_containers`
does. `docker` runs `tests/docker_projects_answer.py`, which reads its
states on every call, so a test can change them between two commands.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.docker_projects_answer import COMPOSE_STEPS, STATUS

ANSWER = Path(__file__).with_name("docker_projects_answer.py")

__all__ = ["docker_on_path", "docker_with_projects"]



# ── The test's side ──────────────────────────────────────────────────────

@dataclass
class Docker:
    """The stand-in of one test, in `folder`, with its states and its record.

        docker.running(core="running")
        docker.fail("up", "port is already allocated")
        docker.compose_steps()          # ["pull", "up"]
    """

    folder: Path

    @property
    def _state(self) -> Path:
        return self.folder / "state.json"

    def running(self, **states: str) -> None:
        """Set the state of each named stacklet; the others do not exist."""
        unknown = set(states.values()) - set(STATUS)
        if unknown:
            raise ValueError(f"no such state: {sorted(unknown)}")
        self._write(projects=states)

    def commits(self, **by_stacklet: str) -> None:
        """Say which commit each stacklet's containers were started from."""
        self._write(commits=by_stacklet)

    def exec_fails(self, message: str) -> None:
        """Make every `docker exec` in a running container exit 1 with `message`."""
        self._write(exec_fail=message)

    def execs(self) -> list[list[str]]:
        """The commands run in containers, as `[container, *command]`, oldest first."""
        return [argv[1:] for argv in self.commands() if argv[:1] == ["exec"]]

    def fail(self, step: str, message: str) -> None:
        """Make every `docker compose <step>` exit 1 with `message` on stderr."""
        self._write(fail={**self._read()["fail"], step: message})

    def commands(self) -> list[list[str]]:
        """Every command Docker got, oldest first, without the word `docker`."""
        log = self.folder / "commands.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def forget(self) -> None:
        """Start the record again, for what a test does after its setup."""
        (self.folder / "commands.log").unlink(missing_ok=True)

    def compose_steps(self) -> list[str]:
        """The `docker compose` steps that changed something, oldest first."""
        steps = [next((a for a in argv if a in COMPOSE_STEPS), "") for argv in self.commands()
                 if argv[:1] == ["compose"]]
        return [step for step in steps if step]

    def _read(self) -> dict:
        return json.loads(self._state.read_text())

    def _write(self, **changes: dict) -> None:
        self._state.write_text(json.dumps({**self._read(), **changes}))


def docker_on_path(monkeypatch, folder: Path):
    """Put the stand-in first on `PATH` for as long as `monkeypatch` lasts.

    The fixture below is this for a test. A helper that is not a fixture
    calls it with its own `monkeypatch.context()`, and `folder` is where the
    stand-in keeps its script and record. The result sets the states:

        start = docker_on_path(scope, tmp_path / "docker")
        docker = start(core="running")
    """
    folder.mkdir(parents=True, exist_ok=True)
    docker = Docker(folder)
    docker._state.write_text(json.dumps({"projects": {}, "fail": {}, "commits": {}, "exec_fail": None}))
    script = folder / "docker"
    script.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{ANSWER}" "{folder}" "$@"\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(folder), prepend=os.pathsep)

    def start(**states: str) -> Docker:
        docker.running(**states)
        return docker

    return start


@pytest.fixture
def docker_with_projects(tmp_path_factory, monkeypatch):
    """The stand-in first on `PATH`; the fixture's value sets the states.

        docker = docker_with_projects(core="running")
    """
    return docker_on_path(monkeypatch, tmp_path_factory.mktemp("docker-with-projects"))
