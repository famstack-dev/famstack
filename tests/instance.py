"""The Simpsons' instance, used from outside: `./stack` as a person runs it.

A test that drives a command as a family member or an agent runs it,
`./stack <id> <command> --json`, gets the instance here:

    with running(tmp_path) as stack:
        stack.serves("stack-docs-paperless:8000", httpserver)
        ran = stack.run("core", "web", "fetch", httpserver.url_for("/recipe"), "--json")
        assert ran.code == 0, ran

What it is:

- The Simpsons' instance (`tests/springfield.py`), with the extension
  dirs a test names and a default model, as the installer leaves it.
- `./stack` as a process for every `run`, with `STACK_DIR` at the instance.
  Discovery, the argument parser, `--json` and the exit code are the ones
  a person gets, and no command's code loads into the test's process.
- The bot runner (`tests/bot_runner.py`): an entry point a command starts
  there runs on this Mac as a process of its own, with the environment
  core renders for the runner (`Stack.env("core")`), plus a stacklet's own
  when its command passes it. Only the runner's code mounts are answered:
  an entry point that writes to `/data` finds nothing there.
- A container address resolves to the test's stand-in for it (`serves`),
  or to nothing (`stopped`), and the model's address to the model
  stand-in (`uses_model`), as names resolve on the stack network.

An extension's tests get one with the extension installed and up
(`tests/extensions/instance.py`).
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from tests import bot_runner
from tests.springfield import REPO, make_stack, write_instance

# What the installer writes into `stack.toml` for the AI: a default model,
# without which the AI client refuses every request. `uses_model` points
# the model's address at a stand-in, which answers to any name.
_INSTALLED_AI = '[ai]\ndefault = "stand-in"\n'

# The discard port on this machine: nothing listens there, so a connection
# is refused at once, as it is to a container that is not running.
_NOWHERE = "127.0.0.1:9"


@dataclass(repr=False)
class Ran:
    """What one `./stack` command did: its exit code and what it printed.
    Its repr is all of it, so `assert ran.code == 0, ran` shows the cause
    (pytest shows an assertion message by its repr)."""

    code: int
    out: str
    err: str

    @property
    def json(self):
        """stdout as JSON, which is all of stdout under `--json`."""
        return json.loads(self.out)

    def __repr__(self) -> str:
        return f"exit {self.code}\n--- stdout\n{self.out}\n--- stderr\n{self.err}"


class Running:
    """A running test instance, used from outside (see the module's docstring)."""

    def __init__(self, root: Path, instance: Path, runner: bot_runner.BotRunner):
        self.root = root
        self.instance = instance
        self._runner = runner

    def run(self, *args: str, input: str | None = None, timeout: int = 60) -> Ran:
        """`./stack <args>`, as a process, against this instance."""
        environ = {**os.environ, "STACK_DIR": str(self.instance),
                   "PATH": f"{self._runner.bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        environ.pop("PYTHONPATH", None)
        done = subprocess.run([str(REPO / "stack"), *args], input=input, capture_output=True,
                              text=True, timeout=timeout, cwd=self.root, env=environ)
        return Ran(done.returncode, done.stdout, done.stderr)

    def serves(self, address: str, httpserver) -> None:
        """The container `address` (`stack-web-search:8080`) is `httpserver`."""
        self._runner.serve(address, f"{httpserver.host}:{httpserver.port}")

    def stopped(self, address: str) -> None:
        """The container `address` is not running: nothing answers there."""
        self._runner.serve(address, _NOWHERE)

    def uses_model(self, model) -> None:
        """The model the bot runner's commands ask is `model` (`tests/model.py`)."""
        environ = make_stack(self.instance).env("core")
        self._runner.serve(urlsplit(environ["OPENAI_URL"]).netloc, urlsplit(model.url).netloc)


def _runner_environ(root: Path, instance: Path) -> dict[str, str]:
    """What the bot runner's processes have: core's env as `stack up core`
    renders it, and what the image sets (`stacklets/core/bot-runner/`)."""
    home = root / "bot-runner" / "home"
    home.mkdir(parents=True)
    return {**make_stack(instance).env("core"), "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(home), "LANG": "C.UTF-8", "PYTHONPATH": str(REPO / "lib")}


@contextlib.contextmanager
def running(root: Path, *, extension_dirs: list[Path] | None = None) -> Iterator[Running]:
    """A Simpsons instance under `root`, its bot runner answered. The first
    of `extension_dirs` (default `root/extensions`) is the one the runner mounts."""
    extension_dirs = extension_dirs or [root / "extensions"]
    instance = write_instance(root, extension_dirs=extension_dirs, extra=_INSTALLED_AI)
    runner = bot_runner.install(root, _runner_environ(root, instance), extensions=extension_dirs[0])
    yield Running(root, instance, runner)
