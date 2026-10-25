"""Every command a stacklet ships loads the way `./stack` loads it.

`./stack <id> <cmd>` runs a file under `stacklets/<id>/cli/` through
`Stack.run_cli_command`: `spec_from_file_location` under the name
`<id>.cli.<cmd>`, `module_from_spec`, `exec_module`, with the stacklet's
own directory on `sys.path` while it runs, as a hook has it, and both the
path and the stacklet's modules given back afterwards.
A test that imports the plugin file itself does none of that, so it passes
on a plugin that cannot load. Two shapes of that have shipped: a plugin
importing `memory.*` before putting the stacklets directory on the path,
and a `@dataclass` with postponed annotations, which looks its module up in
`sys.modules` and finds nothing there.

So each command here goes through the real entry point, against the
fabricated instance, in an interpreter shaped like `./stack`'s.

Why not `--help`: `./stack <id> <cmd> --help` is answered by argparse from
the plugin's docstring, read with `ast`, and never loads the module
(`stack.plugins.plugin_help`). Instead the command is run as
`run_cli_command(<id>, <cmd>, ["--help"])`, and the loader is let all the
way through `exec_module`. Only then is the module's `run` swapped for a
stand-in that records it was reached, so no command does real work: no
Docker, no service, no network. A command whose module does not execute,
or has no `run`, answers with an error here as it would for a person.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from .conftest import REPO, make_stack, write_instance


def _shipped_commands() -> list[tuple[str, str]]:
    """(stacklet, command) for every command the repo ships.

    The rule for what a command is belongs to the framework
    (`stack.plugins.stacklet_commands`, which the CLI builds its parser
    from), so it is asked rather than restated. Discovery is limited to
    the repo's own tree: an extension on the machine running the suite is
    not something the release ships.
    """
    from stack import Stack
    from stack.plugins import stacklet_commands

    repo_only = Stack(root=REPO, data=REPO / ".no-data", instance_dir=REPO / ".no-instance")
    shipped = SimpleNamespace(
        discover=lambda: [s for s in repo_only.discover() if s["source"] == "repo"])
    return [(sid, cmd) for sid, cmds in stacklet_commands(shipped).items() for cmd in cmds]


COMMANDS = _shipped_commands()

# A command that does not load today, with the reason. Strict: when it
# loads again the test fails until its entry here is removed.
KNOWN_BROKEN: dict[tuple[str, str], str] = {}


def _cases():
    for sid, cmd in COMMANDS:
        marks = []
        if (sid, cmd) in KNOWN_BROKEN:
            marks.append(pytest.mark.xfail(reason=KNOWN_BROKEN[(sid, cmd)], strict=True))
        yield pytest.param(sid, cmd, id=f"{sid} {cmd}", marks=marks)


@pytest.fixture
def run_reached(monkeypatch):
    """Let the loader execute a command's module, then stand in for `run`.

    The loader asks `importlib.util.spec_from_file_location` for the
    command's spec. Its loader is wrapped so that `exec_module` runs
    unchanged and, once the module has executed, its `run` is replaced.
    Only the command's own file is touched: a plugin that loads a sibling
    by path gets the real thing.
    """
    reached: dict[str, bool] = {}
    real_spec = importlib.util.spec_from_file_location

    def spec_from_file_location(name, location=None, *args, **kwargs):
        spec = real_spec(name, location, *args, **kwargs)
        if spec is None or Path(str(location)).parent.name != "cli" \
                or name != reached.get("expected"):
            return spec
        real_exec = spec.loader.exec_module

        def exec_module(module):
            real_exec(module)
            reached["had_run"] = callable(getattr(module, "run", None))
            module.run = lambda args, stacklet, config: {"loaded": name, "args": args}

        spec.loader.exec_module = exec_module
        return spec

    monkeypatch.setattr(importlib.util, "spec_from_file_location", spec_from_file_location)
    return reached


@pytest.mark.parametrize("stacklet, command", list(_cases()))
def test_the_command_loads_through_stack(stacklet, command, instance, run_reached,
                                         as_stack_runs_it, monkeypatch):
    """Given the shipped instance, when `./stack <stacklet> <command>` is
    loaded, then its module executes and its `run` is reached.

    A command that cannot load is a command nobody can run, and the unit
    tests that import its file directly cannot see it.
    """
    monkeypatch.setattr("sys.argv", ["stack", stacklet, command, "--help"])
    run_reached["expected"] = f"{stacklet}.cli.{command}"

    answer = make_stack(instance).run_cli_command(stacklet, command, ["--help"])

    assert answer == {"loaded": f"{stacklet}.cli.{command}", "args": ["--help"]}, answer
    assert run_reached.get("had_run"), f"{stacklet}/cli/{command}.py has no run()"


def test_every_stacklet_with_a_cli_directory_ships_a_command():
    """Guards the parametrisation above: if discovery found nothing, every
    case would silently vanish and the file would pass by testing nothing."""
    with_cli = {p.parent.parent.name for p in (REPO / "stacklets").glob("*/cli/*.py")
                if not p.name.startswith("_")}
    assert with_cli and with_cli == {sid for sid, _ in COMMANDS}


# An extension stacklet's command that imports a helper from its stacklet's
# own directory, and puts nothing on `sys.path` itself.
KETTLE_COMMAND = '''
from _polite import politely
from helper import greeting

HELP = "Say hello"


def run(args, stacklet, config):
    return {"said": politely(greeting())}
'''


def test_a_command_imports_the_helpers_beside_it_in_its_stacklet(tmp_path, as_stack_runs_it):
    """Given an extension stacklet whose command imports a helper from the
    stacklet's own directory and one from its own `cli/`, when it runs, then
    both imports work without the command editing `sys.path`, and afterwards
    neither the path nor the helpers are left in the session.

    A command holds no path set-up of its own (R6): the framework gives it
    its stacklet's directory, as it gives a hook.
    """
    instance = write_instance(tmp_path)
    kettle = tmp_path / "extensions" / "kettle"
    (kettle / "cli").mkdir(parents=True)
    (kettle / "stacklet.toml").write_text('id = "kettle"\nname = "Kettle"\ntype = "host"\n')
    (kettle / "helper.py").write_text('def greeting():\n    return "hello"\n')
    (kettle / "cli" / "_polite.py").write_text('def politely(words):\n    return words + ", please"\n')
    (kettle / "cli" / "hello.py").write_text(KETTLE_COMMAND)
    path_before = list(sys.path)

    answer = make_stack(instance).run_cli_command("kettle", "hello", [])

    assert answer == {"said": "hello, please"}
    assert sys.path == path_before
    assert "helper" not in sys.modules and "_polite" not in sys.modules


def test_a_module_loaded_before_a_command_ran_is_left_as_it_was(tmp_path, as_stack_runs_it):
    """Given a module of a stacklet that the process imported before, when a
    command of that stacklet runs, then afterwards the process still has
    that module, the same object.

    The framework forgets what a stacklet's code loaded, so the next
    stacklet's `helper` is its own; what the process held before is not
    the command's to take away, and a second copy of it would be a second
    class of every type it defines.
    """
    instance = write_instance(tmp_path)
    kettle = tmp_path / "extensions" / "kettle"
    (kettle / "cli").mkdir(parents=True)
    (kettle / "stacklet.toml").write_text('id = "kettle"\nname = "Kettle"\ntype = "host"\n')
    (kettle / "helper.py").write_text('def greeting():\n    return "hello"\n')
    (kettle / "cli" / "_polite.py").write_text('def politely(words):\n    return words + ", please"\n')
    (kettle / "cli" / "hello.py").write_text(KETTLE_COMMAND)
    sys.path.insert(0, str(kettle))
    try:
        import helper
    finally:
        sys.path.remove(str(kettle))

    make_stack(instance).run_cli_command("kettle", "hello", [])

    assert sys.modules.get("helper") is helper
    del sys.modules["helper"]
