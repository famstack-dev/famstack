"""`docker_with_projects` answers `docker` the way a Mac with running stacklets does.

Tests of `stack up`, `down` and `list` depend on this stand-in, so what it
says is pinned here, through the same `docker` command the stack runs.
"""

from __future__ import annotations

import subprocess

import stack.docker as docker_client


def test_the_stacklets_that_run_are_the_ones_the_test_names(docker_with_projects):
    docker_with_projects(core="running", docs="failing", photos="stopped")

    assert docker_client.project_states() == {"core": "running", "docs": "failing", "photos": "stopped"}


def test_a_state_can_change_between_two_commands(docker_with_projects):
    docker = docker_with_projects(core="starting")
    assert docker_client.project_states() == {"core": "starting"}

    docker.running(core="running")

    assert docker_client.project_states() == {"core": "running"}


def test_a_compose_step_that_was_made_to_fail_exits_1_with_its_message(docker_with_projects, tmp_path):
    docker = docker_with_projects()
    docker.fail("up", "port is already allocated")
    compose_file = tmp_path / "docker-compose.yml"

    code, error = docker_client.compose_up(compose_file)

    assert (code, error) == (1, "port is already allocated")
    assert docker.compose_steps() == ["up"]


def test_a_command_it_does_not_know_exits_non_zero_and_says_so(docker_with_projects):
    docker_with_projects()

    result = subprocess.run(["docker", "volume", "prune"], capture_output=True, text=True)

    assert result.returncode != 0
    assert "answers no" in result.stderr


def test_the_commit_a_stacklet_started_from_is_the_one_the_test_names(docker_with_projects):
    docker = docker_with_projects(core="running")
    docker.commits(core="abc1234")

    assert docker_client.container_commits() == {"core": "abc1234"}


def test_a_pinned_context_does_not_change_the_answer(docker_with_projects):
    docker = docker_with_projects(core="running")

    result = subprocess.run(["docker", "--context", "orbstack", "compose", "ls", "-a", "--format", "json"],
                            capture_output=True, text=True)

    assert result.returncode == 0
    assert "stack-core" in result.stdout
    assert docker.commands()[-1] == ["compose", "ls", "-a", "--format", "json"]
