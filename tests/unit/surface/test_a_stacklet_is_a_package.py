"""A stacklet's code is one package, `stacklets.<id>`, wherever it lives.

A command, a hook, a migration and a bot are modules of their stacklet's
package, named by their path: `stacklets/memory/cli/find.py` is
`stacklets.memory.cli.find`. So they import the rest of their stacklet
relatively, the way any Python package does, and none of them puts a
directory on `sys.path`:

    from ._polite import politely      # beside the command, in cli/
    from ..helper import greeting      # at the stacklet's top

These tests write small extension stacklets for a fabricated instance and
run their code the way the stack does: a command through `./stack`'s
loader, a hook through the hook runner, a migration through `stack up`'s
migration runner, in an interpreter shaped like `./stack`'s. The bot
runner's half is in `test_every_bot_loads.py`, since a bot loads in the
runner's container.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from .conftest import make_stack, write_instance

HELPER = 'def greeting():\n    return "hello"\n'
POLITE = 'def politely(words):\n    return words + ", please"\n'

COMMAND = '''
from ..helper import greeting
from ._polite import politely

HELP = "Say hello"


def run(args, stacklet, config):
    return {"said": politely(greeting())}
'''


def _stacklet(root: Path, sid: str, files: dict[str, str]) -> Path:
    """An extension stacklet `sid` under `root` with these files."""
    stacklet = root / "extensions" / sid
    (stacklet / "stacklet.toml").parent.mkdir(parents=True, exist_ok=True)
    (stacklet / "stacklet.toml").write_text(f'id = "{sid}"\nname = "{sid.title()}"\ntype = "host"\n')
    for name, text in files.items():
        (stacklet / name).parent.mkdir(parents=True, exist_ok=True)
        (stacklet / name).write_text(text)
    return stacklet


def test_a_command_imports_its_stacklets_modules_relatively(tmp_path, as_stack_runs_it):
    """Given a command that imports a helper beside it and one at its
    stacklet's top, relatively, when `./stack kettle hello` runs, then both
    are found and `sys.path` is as it was: the command is a module of the
    package `stacklets.kettle`, and set up nothing itself."""
    instance = write_instance(tmp_path)
    _stacklet(tmp_path, "kettle", {"helper.py": HELPER, "cli/_polite.py": POLITE,
                                   "cli/hello.py": COMMAND})
    path_before = list(sys.path)

    answer = make_stack(instance).run_cli_command("kettle", "hello", [])

    assert answer == {"said": "hello, please"}
    assert sys.path == path_before


def test_two_stacklets_with_a_module_of_the_same_name_each_import_their_own(
        tmp_path, as_stack_runs_it):
    """Given two stacklets that each have an `oauth.py`, when a command of
    each runs in one process, as in `stack restart all`, then each gets its
    own: the modules are `stacklets.kettle.oauth` and
    `stacklets.teapot.oauth`, two names for two files."""
    instance = write_instance(tmp_path)
    command = 'from ..oauth import WHO\n\n\ndef run(args, stacklet, config):\n    return {"who": WHO}\n'
    for sid in ("kettle", "teapot"):
        _stacklet(tmp_path, sid, {"oauth.py": f'WHO = "{sid}"\n', "cli/who.py": command})
    stck = make_stack(instance)

    answers = [stck.run_cli_command(sid, "who", []) for sid in ("kettle", "teapot")]

    assert answers == [{"who": "kettle"}, {"who": "teapot"}]


def test_a_stacklet_named_like_a_standard_module_leaves_that_module_alone(
        tmp_path, as_stack_runs_it):
    """Given a stacklet called `calendar`, as the standard library's
    `calendar` is, when its command runs, then it imports its own helper
    and the process's `calendar` is still the standard library's: the
    stacklet is `stacklets.calendar`, never `calendar`."""
    instance = write_instance(tmp_path)
    _stacklet(tmp_path, "calendar", {"helper.py": HELPER, "cli/_polite.py": POLITE,
                                     "cli/hello.py": COMMAND})

    answer = make_stack(instance).run_cli_command("calendar", "hello", [])

    import calendar
    assert answer == {"said": "hello, please"}
    assert calendar.isleap(2028)


def test_a_hook_imports_its_stacklets_modules_relatively(tmp_path, as_stack_runs_it):
    """Given an `on_start` hook that imports its stacklet's helper
    relatively, when the hook runs, then it finds it: the hook is
    `stacklets.kettle.hooks.on_start`."""
    from stack.hooks import HookResolver, build_hook_ctx

    said = tmp_path / "said.txt"
    hook = ('from pathlib import Path\n\nfrom ..helper import greeting\n\n\n'
            'def run(ctx):\n    Path(ctx.env["SAID"]).write_text(greeting())\n')
    kettle = _stacklet(tmp_path, "kettle", {"helper.py": HELPER, "hooks/on_start.py": hook})

    ran = HookResolver(kettle).run("on_start", build_hook_ctx("kettle", env={"SAID": str(said)}))

    assert ran and said.read_text() == "hello"


def test_a_migration_imports_its_stacklets_modules_relatively(tmp_path, as_stack_runs_it):
    """Given a migration `0001_greet.py`, a name no `import` statement can
    spell, that imports its stacklet's helper relatively, when `stack up`
    runs the pending migrations, then it runs and is recorded."""
    from stack.migrations import MigrationContext, Migrations

    said = tmp_path / "said.txt"
    migration = ('"""Greet once."""\nfrom pathlib import Path\n\nfrom ..helper import greeting\n\n\n'
                 'def run(ctx):\n    Path(ctx.env["SAID"]).write_text(greeting())\n')
    kettle = _stacklet(tmp_path, "kettle", {"helper.py": HELPER,
                                            "migrations/0001_greet.py": migration})
    migrations = Migrations("kettle", kettle, tmp_path / "data" / "kettle")

    outcome = migrations.run(
        lambda m: MigrationContext(m, stack=None, stacklet_id="kettle", env={"SAID": str(said)}),
        step=lambda line: None)

    assert outcome == {"applied": ["kettle/0001"]}
    assert said.read_text() == "hello"


def test_a_process_that_imports_stacklets_by_name_finds_each_where_the_stack_does(tmp_path):
    """Given a process that names its stacklet directories at its start, as
    core's API does, when it imports a stacklet's module by name, then the
    module comes from the first directory that has the stacklet: the same
    order the stack searches, so an extension never replaces a stacklet the
    release ships."""
    from stack.stacklet_code import import_stacklets

    shipped, extensions = tmp_path / "stacklets", tmp_path / "extensions"
    for root, sid, who in ((shipped, "kettle", "shipped"), (extensions, "kettle", "extension"),
                           (extensions, "teapot", "teapot")):
        (root / sid).mkdir(parents=True, exist_ok=True)
        (root / sid / "stacklet.toml").write_text(f'id = "{sid}"\n')
        (root / sid / "helper.py").write_text(f'WHO = "{who}"\n')
    (extensions / "kettle" / "spout.py").write_text('WHO = "extension"\n')
    held = {n: m for n, m in sys.modules.items() if n == "stacklets" or n.startswith("stacklets.")}
    try:
        for name in held:
            del sys.modules[name]
        import_stacklets([shipped, extensions])

        from stacklets.kettle import helper as kettle
        from stacklets.teapot import helper as teapot

        assert (kettle.WHO, teapot.WHO) == ("shipped", "teapot")
        with pytest.raises(ImportError):
            from stacklets.kettle import spout  # noqa: F401
    finally:
        for name in [n for n in sys.modules if n == "stacklets" or n.startswith("stacklets.")]:
            del sys.modules[name]
        sys.modules.update(held)
