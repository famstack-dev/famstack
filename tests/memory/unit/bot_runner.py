"""`docker` as memory's test instance has it: the bot runner, run on this Mac.

The host reaches the bot runner with two docker commands (`stack.bot_runner`,
memory's `cli/_common.py`), and these are the two answered here:

    docker inspect -f {{.State.Running}} stack-core-bot-runner      true
    docker exec [-i] stack-core-bot-runner python <path> <args>     <path> run here

The entry point runs as `docker exec` runs it, `python <path> <args>`: a
process of its own, with the environment the instance gives the bot runner
and nothing of the test's, its stdin, stdout, stderr and exit code passed
through. `/stacklets/` is the repo's `stacklets/`, which core mounts there;
`PYTHONPATH` names the repo's `lib/`, which core mounts at `/app`; the
working directory is an empty one, as `/app` holds nothing an entry point
writes to. Any other container or command is answered as Docker answers a
container that does not exist.

The instance writes `docker` first on `PATH`, a shell script that runs

    python -I bot_runner.py <settings.json> <docker args>

`settings.json` holds the interpreter, the repo, the working directory and
the environment. Standard library only: `-I` keeps the test session out.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CONTAINER = "stack-core-bot-runner"


def _no_such_container(args: list[str]) -> int:
    print(f"Error response from daemon: No such container (docker {' '.join(args)})",
          file=sys.stderr)
    return 1


def _exec(settings: dict, args: list[str]) -> int:
    """`docker exec [flags] <container> python <path> <args>`, as a process here."""
    rest = list(args)
    while rest and rest[0].startswith("-"):
        rest.pop(0)
    if len(rest) < 3 or rest[0] != CONTAINER or rest[1] != "python" \
            or not rest[2].startswith("/stacklets/"):
        return _no_such_container(["exec", *args])
    entrypoint = Path(settings["repo"]) / rest[2].lstrip("/")
    os.chdir(settings["workdir"])
    os.execve(settings["python"], [settings["python"], "-s", str(entrypoint), *rest[3:]],
              settings["environ"])
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
