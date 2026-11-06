"""The unit lane's rule against Docker: a `docker` that refuses, first on `PATH`.

A unit test must never reach the Docker daemon. Container names are fixed
(`stack-<id>`), so a `docker compose up` from a test replaces the containers
of the instance on the same Mac with ones that use the test's checkout and
temporary folders. The network guard cannot see it: `docker` is a subprocess
that talks to its daemon over a Unix socket.

`tests/lanes/unit.py` installs the guard around each test and fails the test
when `refused()` names a command:

    guard = DockerGuard()
    guard.install()
    subprocess.run(["docker", "ps"])   # exits 125, runs nothing
    guard.refused()                    # ["docker ps"]
    guard.uninstall()

Why `PATH`, and not a patch of `subprocess`: the stack starts `docker` in
several ways, and most of them leave the test's process or never name it in
an argument list. `stack.docker._docker` and direct `subprocess.run` calls
use a list; `ctx.shell` and the `.sh` hooks use a shell line; a test may run
`python -m stack` as a process. All of them look the name up on `PATH`, and
a child process inherits `PATH`.

It also leaves stand-ins alone. A test that puts its own `docker` on `PATH`
puts it in front of this one (`tests/bot_runner.py` does), and a test that
replaces `subprocess.run` never starts anything. Other tools are unaffected,
because the guard's folder holds only `docker`.

The command records itself in a file and exits non-zero, and the test fails
when its phase ends. The refusal cannot live in the exit code, because
`./stack up` turns that into an answer, and a test may assert on the answer.

It does not cover a command with an absolute path, or a process given a
`PATH` that names the real `docker`'s folder. Nothing in `lib/` or
`stacklets/` starts `docker` by an absolute path. The wrappers that export a
fixed `PATH` run under launchd and cron, and no unit test lets them reach
`docker`.
"""

from __future__ import annotations

import os
import shlex
import tempfile
from pathlib import Path

__all__ = ["DockerGuard", "HOW_TO_STAND_IN"]

HOW_TO_STAND_IN = (
    "Unit tests never run Docker, because a real `docker compose` replaces the "
    "containers of the instance on this Mac. Put a stand-in `docker` first on "
    "PATH, as tests/bot_runner.py does, or move the test to a container lane."
)

# What `docker run` returns when Docker itself fails, so a caller reads the
# refusal as a failure of Docker and not of a command in a container.
_REFUSED = 125


class DockerGuard:
    """A folder with a refusing `docker`, which `install()` puts first on `PATH`.

    `refused()` returns the commands it refused since it was last asked.
    One guard serves all the tests of a process. Its folder goes away when
    the process exits.

        guard = DockerGuard()
        guard.install()
        subprocess.run("docker compose up -d", shell=True)
        guard.refused()   # ["docker compose up -d"]
        guard.uninstall()
    """

    def __init__(self) -> None:
        self._folder = tempfile.TemporaryDirectory(prefix="unit-lane-docker-")
        self._bin = self._folder.name
        self._log = Path(self._bin) / "refused.log"
        docker = Path(self._bin) / "docker"
        docker.write_text(_refusing_docker(self._log))
        docker.chmod(0o755)

    def install(self) -> None:
        """Put the refusing `docker` first on this process's `PATH`."""
        os.environ["PATH"] = os.pathsep.join([self._bin, *self._others_on_path()])

    def uninstall(self) -> None:
        """Take the guard's folder off `PATH`, and leave the rest as it is."""
        os.environ["PATH"] = os.pathsep.join(self._others_on_path())

    def refused(self) -> list[str]:
        """The commands refused since the last call, oldest first; clears them."""
        if not self._log.exists():
            return []
        commands = self._log.read_text(encoding="utf-8").splitlines()
        self._log.unlink()
        return commands

    def _others_on_path(self) -> list[str]:
        path = os.environ.get("PATH", "")
        return [entry for entry in path.split(os.pathsep) if entry != self._bin]


def _refusing_docker(log: Path) -> str:
    """The script of the refusing `docker`: it records its arguments in `log`."""
    quoted = shlex.quote(str(log))
    return (
        "#!/bin/sh\n"
        f"printf '%s\\n' \"docker $*\" >> {quoted}\n"
        "echo \"The unit lane refused: docker $*\" >&2\n"
        f"exit {_REFUSED}\n"
    )
