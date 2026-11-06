"""The unit lane refuses every Docker command.

`StackCLI` runs `stack.cli.main` in the test's process, so a unit test that
runs `./stack up` calls the real `docker compose`. Container names are
fixed, so compose replaces the containers of the instance on the same Mac
with ones that use the test's checkout and temporary folders. The lane's
network guard does not see this: `docker` is a subprocess, and it talks to
its daemon over a Unix socket.

These tests run a small test file under the lane's rules, as
`test_unit_lane_stays_offline.py` does, and read what pytest reports for it.
"""

from __future__ import annotations

import os

from tests.unit.test_unit_lane_stays_offline import call_report, run_in_unit_lane

pytest_plugins = ["pytester"]


def test_stack_up_fails_the_test_and_names_the_docker_command(pytester):
    """Given a test that runs `./stack up` of a stacklet with containers,
    when the stack starts `docker`, then the test fails and names the
    command, whatever the stack made of the answer.

    `./stack up` turns a failed command into an exit code, which is what
    such a test asserts on, so the failure cannot depend on the command's
    answer reaching the test."""
    recorder = run_in_unit_lane(pytester, """
        from tests.springfield import write_instance
        from tests.unit.surface.conftest import StackCLI

        def test_brings_up_code(tmp_path, monkeypatch):
            StackCLI(write_instance(tmp_path), monkeypatch)("up", "code")
    """)

    report = call_report(recorder, "test_brings_up_code")
    assert report.failed
    assert "docker " in report.longreprtext


def test_a_refused_command_the_code_ignores_still_fails_the_test(pytester):
    """Given code that ignores a `docker` command's exit code, then the test
    fails with the command and its arguments."""
    recorder = run_in_unit_lane(pytester, """
        import subprocess

        def test_ignores_the_answer():
            subprocess.run(["docker", "ps", "--all"], capture_output=True)
    """)

    report = call_report(recorder, "test_ignores_the_answer")
    assert report.failed
    assert "docker ps --all" in report.longreprtext


def test_a_command_through_a_shell_or_a_child_process_is_refused(pytester):
    """Given `docker` started by a shell line, as `ctx.shell` and a `.sh`
    hook start it, then the test fails. A child process is covered too,
    which a patch of `subprocess` in the test's process would not see."""
    recorder = run_in_unit_lane(pytester, """
        import subprocess
        import sys

        def test_through_a_shell():
            subprocess.run("docker compose up -d", shell=True, capture_output=True)

        def test_through_a_child_process():
            child = "import subprocess; subprocess.run(['docker', 'info'], capture_output=True)"
            subprocess.run([sys.executable, "-c", child])
    """)

    shell = call_report(recorder, "test_through_a_shell")
    child = call_report(recorder, "test_through_a_child_process")
    assert shell.failed and "docker compose up -d" in shell.longreprtext
    assert child.failed and "docker info" in child.longreprtext


def test_a_fixture_that_runs_docker_fails_the_test(pytester):
    """Given a fixture that runs `docker`, in its setup or its teardown, then
    the test fails there and names the command: a fixture reaches the
    daemon as a test body does."""
    recorder = run_in_unit_lane(pytester, """
        import subprocess
        import pytest

        @pytest.fixture
        def up():
            subprocess.run(["docker", "ps"], capture_output=True)

        @pytest.fixture
        def down():
            yield
            subprocess.run(["docker", "stop", "x"], capture_output=True)

        def test_with_setup(up):
            pass

        def test_with_teardown(down):
            pass
    """)

    setup = recorder.matchreport("test_with_setup", when="setup")
    teardown = recorder.matchreport("test_with_teardown", when="teardown")
    assert setup.failed and "docker ps" in setup.longreprtext
    assert teardown.failed and "docker stop x" in teardown.longreprtext


def test_a_stand_in_for_docker_on_the_path_still_answers(pytester):
    """Given a test that puts its own `docker` first on `PATH`, then that one
    answers and the test passes, because a stand-in is how a test that needs
    Docker's answers gets them."""
    recorder = run_in_unit_lane(pytester, """
        import os
        import subprocess

        def test_asks_the_stand_in(tmp_path, monkeypatch):
            docker = tmp_path / "docker"
            docker.write_text("#!/bin/sh\\necho stand-in\\n")
            docker.chmod(0o755)
            monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
            answer = subprocess.run(["docker", "ps"], capture_output=True, text=True)
            assert answer.stdout == "stand-in\\n"
    """)

    assert call_report(recorder, "test_asks_the_stand_in").passed


def test_other_subprocesses_run_and_the_path_is_given_back(pytester):
    """Given a test that runs another tool, then the tool answers, and after
    the test `PATH` is as it was: the rule covers `docker` and nothing else."""
    path_before = os.environ["PATH"]
    recorder = run_in_unit_lane(pytester, """
        import subprocess

        def test_runs_git():
            answer = subprocess.run(["git", "--version"], capture_output=True, text=True)
            assert answer.returncode == 0
    """)

    assert call_report(recorder, "test_runs_git").passed
    assert os.environ["PATH"] == path_before
