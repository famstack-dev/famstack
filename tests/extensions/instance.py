"""An extension's test instance: the extension in the Simpsons' instance, used from outside.

An extension is a stacklet the repo does not ship (ADR-013, ADR-022). Its
tests drive what a family or an agent can call, from the outside, in an
instance this module builds for it:

    with installed(tmp_path, Path("web")) as stack:
        stack.serves("stack-web-search:8080", httpserver)
        ran = stack.run("web", "search", "tent", "--json")
        assert ran.code == 0, ran
        assert ran.json["results"][0]["url"] == "https://example.com/tents"

It is the instance any test drives `./stack` in (`tests/instance.py`:
every command a process, the bot runner, container addresses resolved to
stand-ins), with the extension copied into its first extension directory,
as `stack extensions add` copies it, and up, as `stack up` leaves it
before Docker starts anything (`Stack.up`): its env rendered, its secrets
made, its hooks run. Its containers are the rig lane's.

The extension lane (`tests/lanes/extension.py`) gives an extension's tests
this as the fixture `stack`.
"""

from __future__ import annotations

import contextlib
import shutil
import tomllib
from collections.abc import Iterator
from pathlib import Path

from tests.instance import Running, running
from tests.springfield import make_stack

# What `stack extensions add` leaves out of a copy, and what a test run or
# an earlier `stack up` in the source folder may have left there.
_NOT_COPIED = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".env", ".source.toml")


class Extension(Running):
    """One extension in a running test instance (see the module's docstring)."""

    def __init__(self, running: Running, stacklet_id: str):
        super().__init__(running.root, running.instance, running._runner)
        self.id = stacklet_id

    def up(self) -> None:
        """`stack up <id>` up to Docker: env, secrets, hooks."""
        answer = make_stack(self.instance).up(self.id)
        assert "error" not in answer, answer


@contextlib.contextmanager
def installed(root: Path, extension: Path, *, mounted: bool = True,
              up: bool = True) -> Iterator[Extension]:
    """`extension` (its folder) installed in a Simpsons instance under `root`.

    `mounted=False` installs it in a second extension directory, which the
    bot runner does not see; `up=False` leaves it as `stack extensions add`
    leaves it, never brought up.
    """
    extension = Path(extension).resolve()
    stacklet_id = tomllib.loads((extension / "stacklet.toml").read_text())["id"]
    dirs = [root / "extensions", root / "more-extensions"]
    with running(root, extension_dirs=dirs) as instance:
        shutil.copytree(extension, dirs[0 if mounted else 1] / stacklet_id, ignore=_NOT_COPIED)
        installed = Extension(instance, stacklet_id)
        if up:
            installed.up()
        yield installed
