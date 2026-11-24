"""The `docker` that `tests/docker_with_projects.py` puts on `PATH`.

Run as `python -I docker_projects_answer.py <folder> <docker args>`. It reads
`<folder>/state.json` on every call, writes the command to
`<folder>/commands.log` and answers as a real daemon would. Standard library
only, because `-I` keeps the test session out and every `docker` call starts
a new interpreter. PyYAML is the one exception: it comes with the test
interpreter, and a compose file is YAML.

Compose is answered for the environment of each service, as far as the
stack reads it back: `compose config --format json` resolves each service's
`env_file` and `environment:` with `${VAR}` and `${VAR:-default}` from the
project's `.env`, and `compose up` creates the services it names, or all of
them, with that environment. `inspect <container>` with the `.Config.Env`
format then answers the environment the container was created with, so a
test sees which containers a `compose up` replaced and what they run with.
"""

from __future__ import annotations

import json
import os
import re
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


def _runs(state: dict, container: str) -> bool:
    """Whether `container` (`stack-<id>` or `stack-<id>-<service>`) belongs to a stacklet set."""
    parts = container.split("-")
    return len(parts) > 1 and parts[0] == "stack" and parts[1] in state["projects"]


# ── Compose files: what each service is created with ────────────────────

_DOTENV_LINE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)="((?:[^"\\]|\\.)*)"$')
_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::?-([^}]*))?\}")


def _dotenv(path: Path) -> dict[str, str]:
    """A `.env` as `stack.env_file` writes it: `KEY="value"` lines."""
    if not path.exists():
        return {}
    found = (_DOTENV_LINE.match(line) for line in path.read_text().splitlines())
    return {m.group(1): re.sub(r"\\(.)", r"\1", m.group(2)) for m in found if m}


def _services(compose_file: Path) -> dict[str, dict]:
    """Each service of `compose_file`, by name, with `container` and the
    `environment` compose resolves for it; nothing for a file that is not
    there or not YAML."""
    try:
        import yaml
        parsed = yaml.safe_load(compose_file.read_text()) or {}
    except Exception:
        return {}
    folder = compose_file.parent
    variables = {**_dotenv(folder / ".env"), **os.environ}

    def fill(value) -> str:
        return _VARIABLE.sub(lambda m: variables.get(m.group(1)) or (m.group(2) or ""), str(value))

    services = {}
    for name, service in (parsed.get("services") or {}).items():
        env: dict[str, str] = {}
        for entry in service.get("env_file") or []:
            env.update(_dotenv(folder / (entry if isinstance(entry, str) else entry["path"])))
        block = service.get("environment") or {}
        pairs = block.items() if isinstance(block, dict) else (
            item.partition("=")[::2] for item in block)
        env.update({key: fill(value) for key, value in pairs})
        services[name] = {"container": service.get("container_name", name), "environment": env}
    return services


def _compose_file(argv: list[str]) -> Path:
    return Path(argv[argv.index("-f") + 1]) if "-f" in argv else Path("docker-compose.yml")


def _create(folder: Path, state: dict, argv: list[str]) -> None:
    """Record the containers a `compose up` creates: the services after
    `--force-recreate`, or every service of the file."""
    services = _services(_compose_file(argv))
    named = argv[argv.index("--force-recreate") + 1:] if "--force-recreate" in argv else []
    created = {services[n]["container"]: services[n]["environment"]
               for n in (named or services) if n in services}
    state["containers"] = {**state.get("containers", {}), **created}
    (folder / "state.json").write_text(json.dumps(state))
    with (folder / "recreated.log").open("a") as log:
        log.write(json.dumps([_compose_file(argv).parent.name, sorted(created)]) + "\n")


def _follow(folder: Path, state: dict, step: str, argv: list[str]) -> None:
    """With `follows_compose` set, a `compose up` makes the stacklet of the
    compose file run, and a `compose stop` or `down` stops it."""
    stacklet = _compose_file(argv).parent.name
    if step in ("up", "stop", "down"):
        state["projects"] = {**state["projects"], stacklet: "running" if step == "up" else "stopped"}
        (folder / "state.json").write_text(json.dumps(state))


def _answer(folder: Path, argv: list[str]) -> int:
    """What `docker <argv>` prints and exits with, as a real daemon answers."""
    state = json.loads((folder / "state.json").read_text())
    # The stack pins every command to a context once one was chosen, and the
    # pin outlives the test that chose it in the same process.
    if argv[:1] == ["--context"]:
        argv = argv[2:]
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
    if argv[:1] == ["info"] and state.get("daemon") == "down":
        print("Cannot connect to the Docker daemon", file=sys.stderr)
        return 1
    if argv[:3] == ["context", "ls", "--format"]:
        print(json.dumps({"Name": "orbstack"}))
        return 0
    if argv[:1] == ["info"] or argv[:2] in (["network", "inspect"], ["network", "create"]):
        return 0
    if argv[:1] == ["compose"] and "config" in argv:
        services = _services(_compose_file(argv))
        print(json.dumps({"services": {name: {"container_name": s["container"],
                                              "environment": s["environment"]}
                                       for name, s in services.items()}}))
        return 0
    step = next((a for a in argv if a in COMPOSE_STEPS), None) if argv[:1] == ["compose"] else None
    if step:
        message = state["fail"].get(step)
        print(message or "", file=sys.stderr, end="")
        if step == "up" and not message:
            _create(folder, state, argv)
        if state.get("follows_compose") and not message:
            _follow(folder, state, step, argv)
        return 1 if message else 0
    if (argv[:1] == ["inspect"] and any("{{range .Config.Env}}" in a for a in argv)
            and argv[1] in state.get("containers", {})):
        for key, value in state["containers"][argv[1]].items():
            print(f"{key}={value}")
        return 0
    if argv[:1] == ["exec"] and _runs(state, argv[1]):
        message = state.get("exec_fail")
        print(message or "", file=sys.stderr, end="")
        return 1 if message else 0
    if argv[:1] in (["inspect"], ["exec"], ["logs"]):
        print("Error response from daemon: No such container", file=sys.stderr)
        return 1
    print(f"docker_with_projects answers no: docker {' '.join(argv)}", file=sys.stderr)
    return 125


if __name__ == "__main__":
    sys.exit(_answer(Path(sys.argv[1]), sys.argv[2:]))
