"""Memory's test instance leaves the test process as it found it.

The unit lane runs on long-lived workers, each running test after test in
no fixed order. Whatever an instance leaves running or set is there for
the next test of the same worker, which was written without it: a server
still answering on a port, a thread, the bot runner's `docker` still
first on `PATH`, where a later command would reach it instead of failing
the way it does without a bot runner.
"""

from __future__ import annotations

import os
import shutil
import socket
import threading
import time

from tests.memory.unit.instance import running_memory


def _still_running(before: set[threading.Thread]) -> set[threading.Thread]:
    """The threads started since `before` that have not ended within a
    second: a server's loop ends a moment after its shutdown returns."""
    deadline = time.monotonic() + 1
    while (left := set(threading.enumerate()) - before) and time.monotonic() < deadline:
        time.sleep(0.01)
    return left


def test_once_it_ends_its_servers_threads_and_docker_are_gone(tmp_path):
    """Given a running memory, when it ends, then the curator's API and
    core's door refuse connections, no thread it started is left, and
    `PATH` and the `docker` it finds are what they were before."""
    threads, path, docker = set(threading.enumerate()), os.environ.get("PATH"), shutil.which("docker")

    with running_memory(tmp_path, {"README.md": "# The Simpsons\n"}) as memory:
        ports = [int(memory.curator.rsplit(":", 1)[1]), memory.config["stack"]["core"]["cards_port"]]
        assert shutil.which("docker") != docker

    for port in ports:
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
    assert _still_running(threads) == set()
    assert (os.environ.get("PATH"), shutil.which("docker")) == (path, docker)
