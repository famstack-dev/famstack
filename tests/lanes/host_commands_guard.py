"""The unit lane's rule against host commands: refusing stand-ins, first on `PATH`.

A unit test must never change the Mac that runs it. Three commands do:

    docker      a real `docker compose up` replaces the containers of the
                instance on the same Mac (container names are fixed,
                `stack-<id>`)
    launchctl   `launchctl load` puts a job into the developer's login
                session; a job loaded from a test's temporary folder stays
                there after the test
    brew        installs, upgrades and services change the Mac's software

The network guard cannot see them: each is a subprocess. So this guard
puts a folder first on `PATH` with a refusing script for each, and
`tests/lanes/unit.py` fails the test when `refused()` names a command:

    guard = HostCommandGuard()
    guard.install()
    subprocess.run(["launchctl", "load", "agent.plist"])   # exits 125, runs nothing
    guard.refused()                    # [("launchctl", "launchctl load agent.plist")]
    guard.uninstall()

Why `PATH`, and not a patch of `subprocess`: the stack starts these
commands in several ways, and most of them leave the test's process or
never name it in an argument list. `stack.docker._docker` and direct
`subprocess.run` calls use a list; `ctx.shell` and the `.sh` hooks use a
shell line; a test may run `python -m stack` as a process. All of them look
the name up on `PATH`, and a child process inherits `PATH`.

It also leaves stand-ins alone. A test that puts its own `docker`,
`launchctl` or `brew` on `PATH` puts it in front of this one
(`tests/bot_runner.py`, `tests/unit/surface/test_omlx_key.py`), and a test
that replaces `subprocess.run` never starts anything. Other tools are
unaffected, because the guard's folder holds only these three.

A refused command records itself in a file and exits non-zero, and the
test fails when its phase ends. The refusal cannot live in the exit code,
because `./stack up` turns that into an answer, and a test may assert on
the answer.

A test's fixtures may replace `PATH` (`bin:/usr/bin:/bin`), which drops
the guard's folder, so the lane puts it back before each test body runs
(`cover`), behind the test's own folders. A command is refused by name, so
a test that reaches one fails on a Mac and on Linux alike, also where the
host has no such command. It does not cover a command
with an absolute path, a `PATH` the test body itself replaces, or a
process given its own `PATH` without the guard's folder. Nothing in `lib/` or `stacklets/` starts one of them
by an absolute path. The wrappers that export a fixed `PATH` run under
launchd and cron, and no unit test lets them reach these commands.
"""

from __future__ import annotations

import os
import shlex
import tempfile
from pathlib import Path

__all__ = ["HostCommandGuard", "HOW_TO_STAND_IN"]

# For each refused command: why the lane refuses it, and what to do instead.
HOW_TO_STAND_IN = {
    "docker": (
        "Unit tests never run Docker, because a real `docker compose` replaces the "
        "containers of the instance on this Mac. Put a stand-in `docker` first on "
        "PATH, as tests/bot_runner.py does, or move the test to a container lane."),
    "launchctl": (
        "Unit tests never run launchctl, because `launchctl load` puts a job into "
        "this Mac's login session, where it stays after the test. Put a stand-in "
        "`launchctl` first on PATH, as tests/unit/surface/test_omlx_key.py does."),
    "brew": (
        "Unit tests never run Homebrew, because it installs and starts software on "
        "this Mac. Put a stand-in `brew` first on PATH, as "
        "tests/unit/surface/test_omlx_key.py does."),
}

# Where the real commands live; a test's own folders come before them.
_SYSTEM_FOLDERS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin", "/usr/local/bin", "/opt/homebrew/bin")

# What `docker run` returns when Docker itself fails, so a caller reads the
# refusal as a failure of the command and not of what it was asked to do.
_REFUSED = 125


class HostCommandGuard:
    """A folder with a refusing `docker`, `launchctl` and `brew`, which
    `install()` puts first on `PATH`.

    `refused()` returns the commands it refused since it was last asked,
    each as (command name, command line). One guard serves all the tests
    of a process. Its folder goes away when the process exits.

        guard = HostCommandGuard()
        guard.install()
        subprocess.run("docker compose up -d", shell=True)
        guard.refused()   # [("docker", "docker compose up -d")]
        guard.uninstall()
    """

    def __init__(self) -> None:
        self._folder = tempfile.TemporaryDirectory(prefix="unit-lane-host-commands-")
        self._bin = self._folder.name
        self._log = Path(self._bin) / "refused.log"
        for name in HOW_TO_STAND_IN:
            command = Path(self._bin) / name
            command.write_text(_refusing(name, self._log))
            command.chmod(0o755)

    def install(self) -> None:
        """Put the refusing commands first on this process's `PATH`."""
        os.environ["PATH"] = os.pathsep.join([self._bin, *self._others_on_path()])

    def cover(self) -> None:
        """Put the refusing commands back on `PATH` when a test's fixtures
        replaced it (`bin:/usr/bin:/bin`): just before the first system
        folder, or last when it names none, so the test's own stand-ins stay
        in front of them. Each command is refused by name, whether or not
        this host has it, so a test behaves the same on macOS and on Linux.
        Changes nothing while the folder is on `PATH`."""
        entries = os.environ.get("PATH", "").split(os.pathsep)
        if self._bin in entries:
            return
        at = next((i for i, entry in enumerate(entries) if entry in _SYSTEM_FOLDERS), len(entries))
        os.environ["PATH"] = os.pathsep.join([*entries[:at], self._bin, *entries[at:]])

    def uninstall(self) -> None:
        """Take the guard's folder off `PATH`, and leave the rest as it is."""
        os.environ["PATH"] = os.pathsep.join(self._others_on_path())

    def refused(self) -> list[tuple[str, str]]:
        """The commands refused since the last call, oldest first, as
        (name, command line); clears them."""
        if not self._log.exists():
            return []
        lines = self._log.read_text(encoding="utf-8").splitlines()
        self._log.unlink()
        return [(line.split(" ", 1)[0], line) for line in lines]

    def _others_on_path(self) -> list[str]:
        path = os.environ.get("PATH", "")
        return [entry for entry in path.split(os.pathsep) if entry != self._bin]


def _refusing(name: str, log: Path) -> str:
    """The script of a refusing command: it records its arguments in `log`."""
    quoted = shlex.quote(str(log))
    return (
        "#!/bin/sh\n"
        f"printf '%s\\n' \"{name} $*\" >> {quoted}\n"
        f"echo \"The unit lane refused: {name} $*\" >&2\n"
        f"exit {_REFUSED}\n"
    )
