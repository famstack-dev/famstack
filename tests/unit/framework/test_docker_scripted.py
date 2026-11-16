"""The scripted `docker` answers what a test writes and nothing else."""

import subprocess


def _docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def test_a_scripted_answer_comes_back(docker_scripted):
    docker_scripted.answers("compose ls", stdout="[]", stderr="warn", code=3)

    result = _docker("compose", "-f", "x.yml", "ls")

    assert (result.stdout, result.stderr, result.returncode) == ("[]", "warn", 3)


def test_an_unscripted_command_exits_non_zero_and_says_so(docker_scripted):
    docker_scripted.answers("ps")

    result = _docker("images")

    assert result.returncode != 0
    assert "docker_scripted answers no: docker images" in result.stderr


def test_the_record_lists_commands_and_their_context_pin(docker_scripted):
    docker_scripted.answers("info")
    docker_scripted.answers("ps")

    _docker("--context", "orbstack", "info")
    _docker("ps")

    assert docker_scripted.commands() == [["info"], ["ps"]]
    assert docker_scripted.contexts() == ["orbstack", None]
