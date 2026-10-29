"""`docker` as a test instance has it: the bot runner, run on this Mac.

The host reaches the bot runner with two docker commands (`stack.bot_runner`,
memory's `cli/_common.py`), and these are the two answered here:

    docker inspect -f {{.State.Running}} stack-core-bot-runner             true
    docker exec [-i] [-e NAME]... stack-core-bot-runner python <path> ...  <path> run here
    docker exec [-i] stack-core-bot-runner python -m <module> ...          <module> run here

The entry point runs as `docker exec` runs it, `python <path> <args>`: a
process of its own, with the environment the instance gives the bot runner
and nothing of the test's, its stdin, stdout, stderr and exit code passed
through. The paths are the runner's mounts: `/stacklets/` is the repo's
`stacklets/`, `/extensions/` the instance's first extension directory.
`PYTHONPATH` names the repo's `lib/`, which core mounts at `/app`; the
working directory is an empty one, as `/app` holds nothing an entry point
writes to. `-e NAME` passes NAME from the caller's environment, as the
docker client does. Any other container or command is answered as Docker
answers a container that does not exist.

On the stack network a container reaches another by its name. Here the
test's stand-in for that container listens on localhost, so the instance
names it for the container's address (`BotRunner.serve`), and the address
is rewritten in the entry point's environment: `http://stack-web-search:8080`
becomes `http://127.0.0.1:<port>`.

`install` writes `docker` into a directory a test puts first on `PATH`, a
shell script that runs

    python -I bot_runner.py <settings.json> <docker args>

`settings.json` holds the interpreter, the repo, the extension directory,
the working directory, the environment and the addresses. It is read on
every call, so an address served after the instance started counts from the
next command on. Standard library only: `-I` keeps the test session out.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

CONTAINER = "stack-core-bot-runner"
_REPO = Path(__file__).resolve().parents[1]

# The bot runner's mounts (stacklets/core/docker-compose.yml): what a path
# in a `docker exec` names on this Mac.
_MOUNTS = {"/stacklets/": "stacklets", "/extensions/": "extensions"}


# ── The test's side ──────────────────────────────────────────────────────

class BotRunner:
    """The bot runner of one test instance, answered by `docker` in `bin`.

        runner = install(root, environ, extensions=root / "extensions")
        runner.serve("stack-web-search:8080", "127.0.0.1:54321")
        subprocess.run([...], env={"PATH": f"{runner.bin}:{os.environ['PATH']}"})
    """

    def __init__(self, bin_dir: Path, settings: Path):
        self.bin = bin_dir
        self._settings = settings

    def serve(self, address: str, at: str) -> None:
        """A container `address` (`name:port`) is the stand-in `at` (`host:port`)."""
        settings = json.loads(self._settings.read_text(encoding="utf-8"))
        settings["hosts"][address] = at
        self._settings.write_text(json.dumps(settings), encoding="utf-8")


def install(root: Path, environ: dict[str, str], *, extensions: Path) -> BotRunner:
    """Write `docker` answering for the bot runner under `root/bot-runner`:
    an entry point runs with `environ` as its whole environment, and
    `/extensions/` is `extensions`, the instance's mounted extension dir."""
    bin_dir, workdir = root / "bot-runner" / "bin", root / "bot-runner" / "app"
    bin_dir.mkdir(parents=True)
    workdir.mkdir()
    settings = root / "bot-runner" / "settings.json"
    settings.write_text(json.dumps({
        "python": sys.executable, "stacklets": str(_REPO / "stacklets"),
        "extensions": str(extensions), "workdir": str(workdir),
        "environ": environ, "hosts": {}}), encoding="utf-8")
    docker = bin_dir / "docker"
    docker.write_text(f"#!/bin/sh\nexec '{sys.executable}' -I '{Path(__file__).resolve()}' "
                      f"'{settings}' \"$@\"\n", encoding="utf-8")
    docker.chmod(0o755)
    return BotRunner(bin_dir, settings)


@contextlib.contextmanager
def on_path(runner: BotRunner) -> Iterator[None]:
    """`runner`'s `docker` first on this process's `PATH` while the block
    runs, for a command run in the test's own process."""
    path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{runner.bin}{os.pathsep}{path}"
    try:
        yield
    finally:
        os.environ["PATH"] = path


# ── docker's side ────────────────────────────────────────────────────────

def _no_such_container(args: list[str]) -> int:
    print(f"Error response from daemon: No such container (docker {' '.join(args)})",
          file=sys.stderr)
    return 1


def _split(args: list[str]) -> tuple[list[str], list[str]]:
    """`[flags] <container> <command...>` as `-e` entries and the rest."""
    entries, rest = [], list(args)
    while rest and rest[0].startswith("-"):
        flag = rest.pop(0)
        if flag in ("-e", "--env") and rest:
            entries.append(rest.pop(0))
    return entries, rest


def _entrypoint(settings: dict, path: str) -> Path | None:
    """The file on this Mac a path in the bot runner names, or None."""
    for mount, key in _MOUNTS.items():
        if path.startswith(mount):
            return Path(settings[key]) / path.removeprefix(mount)
    return None


def _environ(settings: dict, entries: list[str]) -> dict[str, str]:
    """The entry point's environment: the runner's, then each `-e` entry,
    with container addresses rewritten to their stand-ins."""
    environ = dict(settings["environ"])
    for entry in entries:
        name, given, value = entry.partition("=")
        if given:
            environ[name] = value
        elif name in os.environ:
            environ[name] = os.environ[name]
    for address, at in settings["hosts"].items():
        environ = {k: v.replace(address, at) for k, v in environ.items()}
    return environ


def _exec(settings: dict, args: list[str]) -> int:
    """`docker exec [flags] <container> python <path> <args>`, or `python -m
    <module> <args>` of the framework, as a process here."""
    entries, rest = _split(args)
    if rest[:2] != [CONTAINER, "python"] or len(rest) < 3:
        return _no_such_container(["exec", *args])
    if rest[2] == "-m" and len(rest) >= 4:
        command = ["-m", *rest[3:]]
    elif entrypoint := _entrypoint(settings, rest[2]):
        command = [str(entrypoint), *rest[3:]]
    else:
        return _no_such_container(["exec", *args])
    os.chdir(settings["workdir"])
    os.execve(settings["python"], [settings["python"], "-s", *command],
              _environ(settings, entries))
    return 0  # not reached: the entry point's process takes this one's place


def main(argv: list[str]) -> int:
    settings = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    args = argv[1:]
    match args:
        case ["inspect", "-f", "{{.State.Running}}", container] if container == CONTAINER:
            print("true")
            return 0
        case ["exec", *rest]:
            return _exec(settings, rest)
    return _no_such_container(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
