"""Every bot a stacklet ships loads the way the bot runner loads it.

The bot runner (stacklets/core/bot-runner/main.py) finds each
`stacklets/<id>/bot/bot.toml` and `<name>.bot.toml`, puts that bot's
directory first on `sys.path` and executes `<name>.py` from it. Nothing
else of the stacklets is on its path: the container has `/app` (the runner
and the framework) and `/stacklets`, and a bot that needs another
stacklet's code has to say so itself. The archivist did not, imported
`memory.*` first, and did not load on the rig, while every unit test,
which sets its own `sys.path`, passed.

So each bot is loaded here by the runner's own code, in a fresh `python -I`
per bot, from a copy of the tree the container mounts. One interpreter per
bot is the point: in the container they share one, and a bot that only
loads because another one was loaded first is broken the day that one is
switched off.

A bot that imports a package the container installs
(stacklets/core/bot-runner/requirements.txt) and the test environment
does not is skipped, naming the package. It is never passed.

Each case starts an interpreter and imports a bot with its dependencies,
which is why one takes up to about a second: that is the price of the
isolation, and the whole file runs in a few.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from stack.stack import Stack

from .conftest import REPO

RUNNER = REPO / "stacklets" / "core" / "bot-runner"
DRIVER = Path(__file__).with_name("bot_runner_load.py")

# What the Dockerfile copies into /app, and the compose file mounts over it.
# `microbot.py`, `room_context.py` and `voice.py` answer the runtime's old
# names for extension bots until 0.6; the runtime is `stack.bot`.
RUNNER_FILES = ("main.py", "microbot.py", "room_context.py", "accounts.py", "voice.py")

# What the runner logs when a bot imports the runtime by its old name.
MOVED = "which moved: use"

# Declarations with no module, on purpose: the runner provisions the
# Matrix account and room and launches nothing. The agent runs in its own
# container (nanobot) and connects with the account this provisions, unless
# `[agent] runtime` names a stacklet whose code answers as it (see its bot.toml).
PROVISION_ONLY = {"agent"}

# A bot that does not load today, with the reason. Strict: when it loads
# again the test fails until its entry here is removed.
KNOWN_BROKEN: dict[str, str] = {}

# What core renders into the runner's environment, for the declarations
# that read it (the agent's id, name and room).
RUNNER_ENV = {
    "MATRIX_SERVER_NAME": "springfield.example",
    "MATRIX_HOMESERVER": "http://127.0.0.1:9",
    "AGENT_BOT_ID": "stacky-bot",
    "AGENT_NAME": "Stacky",
    "AGENT_ROOM": "stacky",
    "AGENT_MODULE": "",
}


def _declarations() -> list[Path]:
    """Every bot declaration, found as the runner finds them."""
    found = []
    for bot_dir in sorted((REPO / "stacklets").glob("*/bot")):
        if (bot_dir / "bot.toml").exists():
            found.append(bot_dir / "bot.toml")
        found.extend(sorted(bot_dir.glob("*.bot.toml")))
    return found


def _cases():
    for toml in _declarations():
        sid = toml.parent.parent.name
        name = f"{sid} {toml.name}"
        marks = []
        if name in KNOWN_BROKEN:
            marks.append(pytest.mark.xfail(reason=KNOWN_BROKEN[name], strict=True))
        yield pytest.param(toml, id=name, marks=marks)


@pytest.fixture(scope="session")
def container(tmp_path_factory) -> dict[str, Path]:
    """The bot runner's filesystem, copied once for the session.

    A copy, not the repo: the repo has `lib/` next to `stacklets/` and the
    container does not, so a bot that reached for the framework through a
    relative path would load here and nowhere else.
    """
    root = tmp_path_factory.mktemp("bot-runner")
    app, stacklets, state = root / "app", root / "stacklets", root / "setup-state"
    app.mkdir()
    for name in RUNNER_FILES:
        shutil.copy2(RUNNER / name, app / name)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules")
    shutil.copytree(REPO / "lib" / "stack", app / "stack", ignore=ignore)
    shutil.copytree(REPO / "stacklets", stacklets, ignore=ignore)
    (state / "extensions").mkdir(parents=True)
    # Every stacklet is enabled: the runner only looks at those with a marker.
    for manifest in stacklets.glob("*/stacklet.toml"):
        (state / f"{manifest.parent.name}.setup-done").touch()
    return {"app": app, "stacklets": stacklets, "state": state}


def _ours(name: str, container: dict[str, Path]) -> bool:
    """A module name the container tree itself provides, as a file or a
    directory anywhere in it, rather than a package pip installs."""
    for root in (container["app"], container["stacklets"]):
        if any(root.rglob(f"{name}.py")) or any(p.is_dir() for p in root.rglob(name)):
            return True
    return False


def _required(name: str) -> bool:
    """Whether the runner's requirements.txt names the package.

    Matched loosely, because a distribution and its import name differ
    (`matrix-nio` is `nio`, `PyYAML` is `yaml`): the import name has to
    appear in a requirement's name.
    """
    text = (RUNNER / "requirements.txt").read_text().lower()
    names = re.findall(r"^([a-z0-9_.\-]+)", text, flags=re.MULTILINE)
    return any(name.lower().replace("_", "-") in n.replace("_", "-") for n in names)


def _load(toml: Path, container: dict[str, Path]) -> dict:
    """Load the shipped bot `toml` declares from the container's copy."""
    return _run_runner(container["stacklets"] / toml.relative_to(REPO / "stacklets"),
                       container)


def _run_runner(bot_toml: Path, container: dict[str, Path], env: dict | None = None) -> dict:
    """Load the bot `bot_toml` declares in a fresh interpreter, with core's
    environment as `RUNNER_ENV` and `env` give it; its report, with what
    the runner logged while loading it under `log`."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
           "HOME": str(container["state"]), **RUNNER_ENV, **(env or {})}
    result = subprocess.run(
        [sys.executable, "-I", str(DRIVER), str(container["app"]),
         str(container["stacklets"]), str(container["state"]), str(bot_toml)],
        capture_output=True, text=True, timeout=60, env=env, cwd=container["state"])
    lines = result.stdout.strip().splitlines()
    assert result.returncode == 0 and lines, result.stderr
    return {**json.loads(lines[-1]), "log": result.stderr}


@pytest.mark.parametrize("toml", list(_cases()))
def test_the_bot_loads_in_the_runner(toml, container):
    """Given the stacklets as the container mounts them, when the bot
    runner loads this bot on its own, then its class is there, and the
    bot reaches the runtime by its name, `stack.bot`.

    A bot that does not load is skipped by the runner with one log line,
    and the family's room goes quiet with nothing else to say why. A bot
    on the runtime's old name loads until 0.6 and then stops loading.
    """
    sid = toml.parent.parent.name
    report = _load(toml, container)

    if sid in PROVISION_ONLY:
        assert report["outcome"] == "provision-only", report
        return
    if report["outcome"] == "missing" and not _ours(report["module"], container):
        assert _required(report["module"]), (
            f"imports {report['module']}, which neither the container's tree nor "
            f"requirements.txt provides\n{report['error']}")
        pytest.skip(f"needs {report['module']}, which the container installs from "
                    "requirements.txt and the test environment does not")
    assert report["outcome"] == "loaded", report.get("error", report)
    assert MOVED not in report["log"], report["log"]


# A bot module is ordinary Python. This one defines a frozen dataclass under
# postponed annotations, and dataclasses looks the class's module up in
# `sys.modules` while it builds the class.
DATACLASS_BOT = '''
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Receipt:
    what: str
    count: int


class ReceiptBot:
    first = Receipt("donuts", 12)
'''


def test_a_bot_that_defines_a_dataclass_loads(container, tmp_path):
    """Given a bot whose module defines a dataclass, when the bot runner
    loads it, then its class is there.

    Whatever a module may do while it is imported, a bot module may do
    too. A runner that executes the file without registering the module
    loses such a bot with one log line, before it ever reaches its room.
    """
    stacklets, state = tmp_path / "stacklets", tmp_path / "setup-state"
    bot = stacklets / "receipts" / "bot"
    bot.mkdir(parents=True)
    (bot / "bot.toml").write_text('id = "receipt-bot"\n')
    (bot / "receipt.py").write_text(DATACLASS_BOT)
    (state / "extensions").mkdir(parents=True)
    (state / "receipts.setup-done").touch()

    report = _run_runner(bot / "bot.toml",
                         {"app": container["app"], "stacklets": stacklets, "state": state})

    assert report["outcome"] == "loaded", report.get("error", report)
    assert report["class"] == "ReceiptBot"


# A bot that imports the rest of its stacklet relatively, as a module of the
# package `stacklets.<id>` (`stack.stacklet_code`).
RELATIVE_BOT = '''
from ..helper import greeting
from ._spout import pour


class KettleBot:
    said = pour(greeting())
'''


def test_a_bot_imports_the_rest_of_its_stacklet_relatively(container, tmp_path):
    """Given an extension bot that imports a helper at its stacklet's top
    and one beside it, relatively, when the bot runner loads it, then its
    class is there: the bot is `stacklets.kettle.bot.kettle`, so its
    imports need nothing on the container's `sys.path`."""
    stacklets, state = tmp_path / "stacklets", tmp_path / "setup-state"
    kettle = state / "extensions" / "kettle"
    (kettle / "bot").mkdir(parents=True)
    stacklets.mkdir()
    (kettle / "helper.py").write_text('def greeting():\n    return "hello"\n')
    (kettle / "bot" / "_spout.py").write_text('def pour(words):\n    return words + "!"\n')
    (kettle / "bot" / "bot.toml").write_text('id = "kettle-bot"\n')
    (kettle / "bot" / "kettle.py").write_text(RELATIVE_BOT)
    (state / "kettle.setup-done").touch()

    report = _run_runner(kettle / "bot" / "bot.toml",
                         {"app": container["app"], "stacklets": stacklets, "state": state})

    assert report["outcome"] == "loaded", report.get("error", report)
    assert report["class"] == "KettleBot"


# An extension bot as it was written before the runtime moved into the
# framework: it imports the runtime from the runner's directory.
OLD_IMPORT_BOT = '''
from microbot import MicroBot


class KettleBot(MicroBot):
    name = "kettle"
'''


def test_an_extension_bot_on_the_runtimes_old_name_still_loads(container, tmp_path):
    """Given an extension bot that imports `microbot` the way bots did
    before the runtime moved to `stack.bot`, when the bot runner loads it,
    then it loads, and the runner's log names its file and the import to
    use instead.

    Extensions live outside the repository (ADR-013), so an update cannot
    change them. Until 0.6 such a bot keeps running, and the admin learns
    from the log which file to change before it stops.
    """
    stacklets, state = tmp_path / "stacklets", tmp_path / "setup-state"
    bot = state / "extensions" / "kettle" / "bot"
    bot.mkdir(parents=True)
    stacklets.mkdir()
    (bot / "bot.toml").write_text('id = "kettle-bot"\n')
    (bot / "kettle.py").write_text(OLD_IMPORT_BOT)
    (state / "kettle.setup-done").touch()

    report = _run_runner(bot / "bot.toml",
                         {"app": container["app"], "stacklets": stacklets, "state": state})

    if report["outcome"] == "missing":
        pytest.skip(f"needs {report['module']}, which the container installs")
    assert report["outcome"] == "loaded", report.get("error", report)
    assert report["class"] == "KettleBot"
    assert str(bot / "kettle.py") in report["log"]
    assert "from stack.bot import MicroBot" in report["log"]


# The code a runtime stacklet ships to answer as the agent's account.
RUNTIME_AGENT_BOT = '''
class AgentBot:
    """Answers as whichever account declares it."""
'''


def _with_runtime(container, tmp_path) -> dict[str, Path]:
    """The container's tree with an extension stacklet `kettle` that ships
    `bot/agent.py`. Nothing marks kettle as set up: it is only code."""
    state = tmp_path / "setup-state"
    (state / "extensions" / "kettle" / "bot").mkdir(parents=True)
    (state / "extensions" / "kettle" / "stacklet.toml").write_text(
        'id = "kettle"\nname = "Kettle"\ntype = "host"\nstage = "incubating"\n')
    (state / "extensions" / "kettle" / "bot" / "agent.py").write_text(RUNTIME_AGENT_BOT)
    for marker in container["state"].glob("*.setup-done"):
        (state / marker.name).touch()
    return {"app": container["app"], "stacklets": container["stacklets"], "state": state}


def _agent_module(tmp_path: Path, runtime: str) -> str:
    """What core renders as the agent's module when stack.toml says
    `[agent] runtime = <runtime>`."""
    instance = tmp_path / "instance"
    instance.mkdir()
    (instance / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\n\n[agent]\nruntime = "{runtime}"\n')
    stack = Stack(REPO, tmp_path / "data", instance_dir=instance)
    return stack.env("core")["AGENT_MODULE"]


@pytest.mark.parametrize("runtime, outcome", [("nanobot", "provision-only"),
                                              ("kettle", "loaded")])
def test_the_runtime_stack_toml_names_answers_as_the_agent(container, tmp_path,
                                                           runtime, outcome):
    """Given `[agent] runtime` in stack.toml, when the bot runner loads the
    agent's declaration with the environment core renders from it, then
    nanobot's account is only provisioned (nanobot answers from its own
    container), and a stacklet named there answers with its
    `bot/agent.py`.
    """
    tree = _with_runtime(container, tmp_path)
    agent_toml = tree["stacklets"] / "agent" / "bot" / "bot.toml"

    report = _run_runner(agent_toml, tree,
                         env={"AGENT_MODULE": _agent_module(tmp_path, runtime)})

    assert report["outcome"] == outcome, report.get("error", report)
    if outcome == "loaded":
        assert report["class"] == "AgentBot"


def test_the_account_stays_the_agents_whichever_code_answers(container, tmp_path):
    """Given a runtime stacklet answers as the agent, when the bot runner
    loads it, then it signs in as the agent's handle and keeps its login
    state in the agent's data dir, where nanobot's account lives too.

    The account belongs to the stacklet that declares it, never to the code
    answering as it. Switching the runtime back and forth keeps the
    account, its rooms and its devices, and every other part of the stack
    goes on addressing the agent as before.
    """
    tree = _with_runtime(container, tmp_path)

    report = _run_runner(tree["stacklets"] / "agent" / "bot" / "bot.toml", tree,
                         env={"AGENT_MODULE": "kettle/agent"})

    assert report["outcome"] == "loaded", report.get("error", report)
    assert report["account"] == "@stacky-bot:springfield.example"
    assert Path(report["session_dir"]) == tree["state"] / "data" / "agent" / "bot"


def test_every_shipped_bot_is_a_case():
    """Guards the parametrisation: no declarations found would pass by
    testing nothing."""
    assert {t.parent.parent.name for t in _declarations()} >= {"core", "docs"}

