"""A fabricated instance, and an interpreter shaped like the one `./stack` runs.

The tests in this directory drive the stack through the surfaces a person,
a bot or the agent actually uses: `./stack <id> <cmd>`, the bot runner, a
command's answer and the commits it leaves behind. Two things make that
honest in-process.

The instance. `instance` is a stack.toml, users.toml and secrets file for
the Simpsons, built once per session, with the stacklets the repo ships.
Nothing in it points at a real service: no Docker, no model, no network
beyond localhost.

The interpreter. A pytest session is not the process `./stack` starts. By
the time a test here runs, other tests have put stacklet directories on
`sys.path` and imported stacklet modules by their bare names (`lib`,
`memory`, `bot`), and a plugin that forgot to put its own imports on the
path finds them anyway. That is how a command passed hundreds of tests and
failed on its first real call. `as_stack_runs_it` takes the session back to
what `PYTHONPATH=lib python -m stack` has: the framework, the standard
library and the installed packages, and no stacklet module imported yet.
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
LIB = REPO / "lib"
STACKLETS = REPO / "stacklets"

if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


# ── The fabricated instance ──────────────────────────────────────────────

USERS = """
[[users]]
id       = "homer"
name     = "Homer Simpson"
email    = "homer@springfield.example"
password = "donuts"
role     = "admin"

[[users]]
id       = "marge"
name     = "Marge Simpson"
email    = "marge@springfield.example"
password = "pretzels"
role     = "member"
"""


def write_instance(root: Path, *, extra: str = "",
                   server_name: str | None = "springfield.example") -> Path:
    """Write a Simpsons instance under `root` and return its directory.

    `extension_dirs` names a directory that stays empty, so discovery sees
    only what the repo ships, whatever the machine running the suite has
    in its home. `host` is set so no command has to work out a LAN address.
    `extra` is appended to stack.toml for a test that needs one more section.
    `server_name` is the Matrix server's name; None leaves `[messages]` out.
    """
    messages = f'[messages]\nserver_name = "{server_name}"\n' if server_name else ""
    instance = root / "instance"
    (instance / ".stack").mkdir(parents=True, exist_ok=True)
    (root / "extensions").mkdir(exist_ok=True)
    (root / "data").mkdir(exist_ok=True)
    (instance / "stack.toml").write_text(
        f'[core]\nname = "stack"\ndomain = ""\nhost = "127.0.0.1"\n'
        f'data_dir = "{root / "data"}"\nextension_dirs = ["{root / "extensions"}"]\n'
        f'timezone = "America/Chicago"\nlanguage = "en"\n\n{messages}{extra}',
        encoding="utf-8")
    (instance / "users.toml").write_text(USERS, encoding="utf-8")
    (instance / ".stack" / "secrets.toml").write_text(
        'global__ADMIN_PASSWORD = "donuts"\n', encoding="utf-8")
    return instance


def make_stack(instance: Path):
    """The Stack `./stack` builds for `instance`, over the repo's stacklets."""
    from stack import Stack
    from stack.output import CollectorOutput

    data = tomllib.loads((instance / "stack.toml").read_text())["core"]["data_dir"]
    return Stack(root=REPO, data=Path(data), instance_dir=instance, output=CollectorOutput())


@pytest.fixture(scope="session")
def instance(tmp_path_factory) -> Path:
    """The Simpsons instance, built once for the session."""
    return write_instance(tmp_path_factory.mktemp("springfield"))


# ── The interpreter `./stack` runs in ────────────────────────────────────

# Compared as a string: resolving the path of every module in the session
# costs more than all the commands together.
_OURS = f"{STACKLETS}{os.sep}"


def _ours(module) -> bool:
    """A module loaded from a stacklet, not from the framework.

    A stacklet directory has no `__init__.py`, so `import memory` with
    `stacklets/` on the path makes a namespace package: no `__file__`, only
    a `__path__`. It is the one that matters most, since it is what lets
    `from memory.lib import ...` succeed without the path set up.
    """
    origin = getattr(module, "__file__", None)
    if isinstance(origin, str):
        return origin.startswith(_OURS)
    return any(str(p).startswith(_OURS) for p in getattr(module, "__path__", None) or [])


def _production_path() -> list[str]:
    """`sys.path` as `PYTHONPATH=lib python -m stack` has it, cwd left out.

    The standard library and the installed packages live under the
    interpreter's prefixes; everything else on the session's path was put
    there by a test. The working directory is dropped too: the scheduler
    and the agent run `./stack` from elsewhere, so a command that only
    works from the repo root is broken.
    """
    prefixes = {Path(p).resolve() for p in (sys.prefix, sys.base_prefix, sys.exec_prefix)}
    kept = [p for p in sys.path
            if p and any(Path(p).resolve().is_relative_to(pre) for pre in prefixes)]
    return [str(LIB), *kept]


@pytest.fixture
def as_stack_runs_it(monkeypatch):
    """Run the test body in an interpreter shaped like `./stack`'s.

    Restores the session afterwards: the stacklet modules other tests had
    imported come back, and the ones imported meanwhile are forgotten.
    """
    before = set(sys.modules)
    monkeypatch.setattr(sys, "path", _production_path())
    for name, module in list(sys.modules.items()):
        if _ours(module):
            monkeypatch.delitem(sys.modules, name)
    yield
    for name in set(sys.modules) - before:
        if _ours(sys.modules[name]):
            del sys.modules[name]
