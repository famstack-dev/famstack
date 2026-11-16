"""The `docker` that `tests/docker_with_projects.py` puts on `PATH`.

Run as `python -I docker_projects_answer.py <folder> <docker args>`. It reads
`<folder>/state.json` on every call, writes the command to
`<folder>/commands.log` and answers as a real daemon would. Standard library
only, because `-I` keeps the test session out and every `docker` call starts
a new interpreter.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# How a real `docker compose ls` words each state of `stack list`.
STATUS = {
    "running": "running(1)",
    "starting": "starting(1), running(1)",
    "failing": "restarting(1)",
    "stopped": "exited(1)",
}

COMPOSE_STEPS = ("up", "pull", "down", "stop", "build")


# ── The stand-in's side ────────────────────────────────────────────────── ──────────────────────────────────────────────────

def _compose_ls(projects: dict[str, str]) -> str:
    return json.dumps([{"Name": f"stack-{sid}", "Status": STATUS[state]}
                       for sid, state in projects.items()])


def _answer(folder: Path, argv: list[str]) -> int:
    """What `docker <argv>` prints and exits with, as a real daemon answers."""
    state = json.loads((folder / "state.json").read_text())
    with (folder / "commands.log").open("a") as log:
        log.write(json.dumps(argv) + "\n")
    if argv[:2] == ["compose", "ls"]:
        print(_compose_ls(state["projects"]))
        return 0
    if argv[:1] == ["ps"]:
        # `container_commits`: one `stack-<id>-<service>` row per stamped stacklet.
        for sid, commit in state.get("commits", {}).items():
            print(f"stack-{sid}-main\t{commit}")
        return 0
    if argv[:1] == ["info"] or argv[:2] in (["network", "inspect"], ["network", "create"]):
        return 0
    if argv[:1] == ["compose"] and "config" in argv:
        print(json.dumps({"services": {}}))
        return 0
    step = next((a for a in argv if a in COMPOSE_STEPS), None) if argv[:1] == ["compose"] else None
    if step:
        message = state["fail"].get(step)
        print(message or "", file=sys.stderr, end="")
        return 1 if message else 0
    if argv[:1] in (["inspect"], ["exec"], ["logs"]):
        print("Error response from daemon: No such container", file=sys.stderr)
        return 1
    print(f"docker_with_projects answers no: docker {' '.join(argv)}", file=sys.stderr)
    return 125


if __name__ == "__main__":
    sys.exit(_answer(Path(sys.argv[1]), sys.argv[2:]))
