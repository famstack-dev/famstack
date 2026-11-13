"""`docker` as a Mac has it where no container runs, for tests that only look.

The unit lane refuses the real `docker` (`tests/lanes/host_commands_guard.py`). A
command like `stack list` or `stack doctor` still asks Docker what runs. Such
a test takes this stand-in, and its answer no longer depends on what runs on
the Mac that runs the suite:

    @pytest.mark.usefixtures("docker_with_no_containers")
    def test_lists_the_stacklets(stack_cli):
        assert stack_cli("list", "--json").code == 0

The answers are the real client's when the daemon holds nothing:

    docker ps ...                     nothing
    docker compose ls --format json   []
    docker context ls --format json   the one context, as on a plain install
    docker inspect | exec | logs ...  "No such container", exit 1

Anything else exits non-zero, so a test that starts or removes something
fails in the same way as without the stand-in: a test for `stack up` needs a
stand-in of its own that says what it expects.

A test that needs the bot runner takes `tests/bot_runner.py` instead.
"""

from __future__ import annotations

import pytest

__all__ = ["docker_with_no_containers"]

_DOCKER = """#!/bin/sh
case "$1 $2" in
  "compose ls")  echo '[]' ;;
  "context ls")  echo '{"Name":"default","Current":true}' ;;
  "ps "*)        ;;
  "inspect "*|"exec "*|"logs "*)
                 echo "Error response from daemon: No such container" >&2; exit 1 ;;
  *)             echo "docker_with_no_containers answers no: docker $*" >&2; exit 125 ;;
esac
"""


@pytest.fixture
def docker_with_no_containers(_stand_in_folder, monkeypatch) -> None:
    """Put the stand-in first on `PATH` for the test.

        subprocess.run(["docker", "compose", "ls", "--format", "json"])   # prints []
    """
    monkeypatch.setenv("PATH", str(_stand_in_folder), prepend=":")


@pytest.fixture(scope="session")
def _stand_in_folder(tmp_path_factory):
    folder = tmp_path_factory.mktemp("docker-with-no-containers")
    docker = folder / "docker"
    docker.write_text(_DOCKER)
    docker.chmod(0o755)
    return folder
