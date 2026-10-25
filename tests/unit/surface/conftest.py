"""A fabricated instance, and an interpreter shaped like the one `./stack` runs.

The tests in this directory drive the stack through the surfaces a person,
a bot or the agent actually uses: `./stack <id> <cmd>`, the bot runner, a
command's answer and the commits it leaves behind. Two things make that
honest in-process.

The instance. `instance` is a stack.toml, users.toml and secrets file for
the Simpsons (`tests/springfield.py`), built once per session, with the
stacklets the repo ships. Nothing in it points at a real service: no
Docker, no model, no network beyond localhost.

The interpreter. A pytest session is not the process `./stack` starts. By
the time a test here runs, other tests have put stacklet directories on
`sys.path` and imported stacklet modules by their bare names (`lib`,
`memory`, `bot`), and a plugin that forgot to put its own imports on the
path finds them anyway. That is how a command passed hundreds of tests and
failed on its first real call. `as_stack_runs_it` takes the session back to
what `PYTHONPATH=lib python -m stack` has: the framework, the standard
library and the installed packages, and no stacklet module imported yet.

A test that needs a running memory takes it from memory's test instance
(`tests/memory/unit/instance.py`), not from here.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
LIB = REPO / "lib"
STACKLETS = REPO / "stacklets"

if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


# ── The fabricated instance ──────────────────────────────────────────────

# Where every test that runs `./stack` gets its instance (`tests.springfield`).
from tests.springfield import make_stack, write_instance  # noqa: E402, F401


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

    In the package `stacklets` (`stack.stacklet_code`), told by its name:
    everything below a stacklet's package, the directories without
    `__init__.py` (`cli/`) too. `stacklets` and `stacklets.<id>` are not:
    they are registrations, a name and a directory, which the framework
    keeps between loads and which run no code of the stacklet.
    A stacklet directory has no `__init__.py`, so `import memory` with
    `stacklets/` on the path makes a namespace package: no `__file__`, only
    a `__path__`. It is the one that matters most, since it is what lets
    `from memory.lib import ...` succeed without the path set up.
    """
    name = getattr(module, "__name__", "")
    if name == "stacklets" or name.startswith("stacklets."):
        return name.count(".") >= 2
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


# ── `./stack` in this process ────────────────────────────────────────────

@dataclass
class Ran:
    """What one `./stack` command left: its exit code and its two streams."""

    code: int
    out: str
    err: str

    @property
    def answer(self) -> dict:
        """stdout read as the JSON a `--json` command prints."""
        return json.loads(self.out)


class StackCLI:
    """`./stack <args>` run in this process through `stack.cli.main`, the
    function `./stack` calls, against one instance (`STACK_DIR`).

    `stdin` is what the command reads. With `terminal=True` stdin says it
    is a terminal; the test then feeds `input` and `getpass` itself.
    """

    def __init__(self, instance: Path, monkeypatch):
        self.instance = instance
        self._monkeypatch = monkeypatch
        monkeypatch.setenv("STACK_DIR", str(instance))
        monkeypatch.chdir(REPO)

    def __call__(self, *args: str, stdin: str = "", terminal: bool = False) -> Ran:
        from stack import cli
        fed = io.StringIO(stdin)
        fed.isatty = lambda: terminal  # type: ignore[method-assign]
        self._monkeypatch.setattr(sys, "stdin", fed)
        self._monkeypatch.setattr(sys, "argv", ["stack", *args])
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cli.main()
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        return Ran(code, out.getvalue(), err.getvalue())

    def toml(self) -> str:
        """The instance's stack.toml as it is now."""
        return (self.instance / "stack.toml").read_text()


@pytest.fixture
def server_name() -> str | None:
    """The instance's Matrix server name; a test overrides it with
    `@pytest.mark.parametrize("server_name", [None])`."""
    return "springfield.example"


@pytest.fixture
def stack_cli(tmp_path, monkeypatch, as_stack_runs_it, server_name) -> StackCLI:
    """`./stack` against a fresh Simpsons instance in `tmp_path`."""
    return StackCLI(write_instance(tmp_path, server_name=server_name), monkeypatch)


# ── The tools server's backends ──────────────────────────────────────────
# Core's tools server reads the host API (the stack's state, jobs) and the
# memory's cards API (the activity). Neither runs in the unit lane, and
# their addresses in a container are names the lane may not look up. So by
# default both point at a port on this machine where nothing listens: the
# page renders each widget's "does not answer" state. A test that needs one
# stands it in and sets the address itself.

def a_closed_port() -> int:
    """A port on 127.0.0.1 nothing listens on, a moment after asking."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def no_backends_behind_the_tools_server(monkeypatch):
    closed = a_closed_port()
    monkeypatch.setenv("STACK_API_HOST", "127.0.0.1")
    monkeypatch.setenv("STACK_API_PORT", str(closed))
    monkeypatch.setenv("MEMORY_CARDS_URL", f"http://127.0.0.1:{closed}")
