"""A stacklet's code is the Python package `stacklets.<id>`.

Every stacklet is one package, wherever its directory is: the repo's
`stacklets/` or an extension directory. Its commands, hooks, migrations
and bots are modules of it, named by their path, so they import the rest
of their stacklet relatively and put nothing on `sys.path`:

    # stacklets/core/cli/schedule.py, the module stacklets.core.cli.schedule
    from ._common import dispatch        # stacklets/core/cli/_common.py
    from ..scheduler import Scheduler    # stacklets/core/scheduler.py

One name is one file of one stacklet. Two stacklets that each have an
`oauth.py` have `stacklets.code.oauth` and `stacklets.photos.oauth`, and a
stacklet named like a standard module (`code`) leaves that module alone.

The framework loads a stacklet's file with `load`, which is what
`Stack.run_cli_command`, `hooks.run_python_file` and the bot runner call:

    module = load(Path("stacklets/core"), Path("stacklets/core/cli/mail.py"))
    module.__name__     # 'stacklets.core.cli.mail'

A process that imports stacklet modules by name, rather than loading one
file, says where its stacklets are once, at its start (`import_stacklets`).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType

PACKAGE = "stacklets"


def load(stacklet_dir: Path, path: Path) -> ModuleType:
    """Execute `path`, a file of the stacklet at `stacklet_dir`, as its
    module, and answer the module.

    The file is executed afresh each time, so an edited command or hook
    runs as edited. The module is registered in `sys.modules` while it
    runs, as an import would register it (a `@dataclass` under postponed
    annotations looks its module up there), and removed again when it
    raises. The stacklet's id is its directory's name.
    """
    stacklet_dir = Path(stacklet_dir).resolve()
    _stacklet_package(stacklet_dir)
    name = module_name(stacklet_dir, path)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def module_name(stacklet_dir: Path, path: Path) -> str:
    """The module a file of the stacklet at `stacklet_dir` is:
    `stacklets/memory/cli/find.py` is `stacklets.memory.cli.find`."""
    stacklet_dir = Path(stacklet_dir).resolve()
    inside = Path(path).resolve().relative_to(stacklet_dir).with_suffix("")
    return ".".join((PACKAGE, stacklet_dir.name, *inside.parts))


def import_stacklets(dirs: Iterable[Path]) -> None:
    """Make the stacklets in `dirs` importable by name, as `stacklets.<id>`.

    `dirs` are the directories stacklets are found in, in the order the
    stack searches them. A stacklet is a directory with a `stacklet.toml`;
    one in two of them is the first one's, as it is for the stack, and
    nothing of the other is importable.
    """
    seen: set[str] = set()
    for d in dirs:
        for manifest in sorted(Path(d).glob("*/stacklet.toml")):
            stacklet_dir = manifest.parent.resolve()
            if stacklet_dir.name not in seen:
                seen.add(stacklet_dir.name)
                _stacklet_package(stacklet_dir)


def _root() -> ModuleType:
    """The package `stacklets`. Nothing is found in it by searching: each
    stacklet in it is one the framework registered, at its directory."""
    return sys.modules.get(PACKAGE) or _register(PACKAGE, [])


def _stacklet_package(stacklet_dir: Path) -> None:
    """Make `stacklets.<id>` the package at `stacklet_dir`.

    A package of that id at another directory is replaced, together with
    the modules loaded from it: they belong to another stacklet, as when
    one process loads a stacklet from two trees.
    """
    name = f"{PACKAGE}.{stacklet_dir.name}"
    held = sys.modules.get(name)
    if held is not None and list(getattr(held, "__path__", [])) == [str(stacklet_dir)]:
        return
    for loaded in [n for n in sys.modules if n.startswith(f"{name}.")]:
        del sys.modules[loaded]
    setattr(_root(), stacklet_dir.name, _register(name, [str(stacklet_dir)]))


def _register(name: str, path: list[str]) -> ModuleType:
    """A package `name` whose modules are found in `path`, registered."""
    spec = importlib.machinery.ModuleSpec(name, None, is_package=True)
    spec.submodule_search_locations = path
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    return package
