"""`./stack` as a process, from a checkout of its own, for surface tests.

A command that asks a question with `input()` and deletes on the answer
is tested best the way a shell runs it: a process with the answer on its
standard input, from a terminal (a pty, for a command that refuses to
ask without one), or a real Ctrl+C (SIGINT) at the question. In process,
`cli.main` reads `sys.argv`, and patching it is a stand-in for code.

    checkout = Checkout.under(tmp_path)          # tmp_path/checkout
    run = checkout.stack("uninstall", "--yes", answer="no")
    assert run.returncode == 0, run.stderr
    checkout.stack_interrupted("uninstall", "--yes", at=b"remove all data: ")
    run = checkout.stack_in_terminal("destroy", "photos", answer="destroy")

The checkout is a folder in `tmp_path` whose `stacklets` is a link to the
repo's, and the process runs in it, so the stack finds that folder as its
checkout (`cli.find_repo_root` walks up from the working folder). Code
that writes into "the checkout" writes there, never into the repo the
suite runs from. The process gets the test's environment: `HOME`,
`STACK_DIR` and a docker stand-in first on `PATH` are the test's to set.
"""

from __future__ import annotations

import os
import pty
import select
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from tests.springfield import REPO

__all__ = ["Checkout"]

# Long enough for a loaded machine, short enough that a hang fails fast.
RUN_SECONDS = 30

# How long a press of Ctrl+C has to end the process before the next one.
PRESS_AGAIN_SECONDS = 1


@dataclass(frozen=True)
class Checkout:
    """A checkout folder that `./stack` runs in; see the module header."""

    path: Path

    @classmethod
    def under(cls, root: Path) -> "Checkout":
        """Make `root/checkout` with a link to the repo's stacklets."""
        path = root / "checkout"
        path.mkdir()
        (path / "stacklets").symlink_to(REPO / "stacklets")
        return cls(path)

    def stack(self, *args: str, answer: str | None = "") -> subprocess.CompletedProcess:
        """Run `./stack <args>` with `answer` typed at its one question;
        None closes standard input without an answer."""
        typed = "" if answer is None else f"{answer}\n"
        return subprocess.run(self._command(args), input=typed, capture_output=True,
                              text=True, cwd=self.path, env=_environment(),
                              timeout=RUN_SECONDS)

    def stack_in_terminal(self, *args: str, answer: str) -> subprocess.CompletedProcess:
        """Run `./stack <args>` with a terminal as standard input, and
        `answer` typed into it."""
        typing, terminal = pty.openpty()
        try:
            os.write(typing, f"{answer}\n".encode())
            return subprocess.run(self._command(args), stdin=terminal, capture_output=True,
                                  text=True, cwd=self.path, env=_environment(),
                                  timeout=RUN_SECONDS)
        finally:
            os.close(typing)
            os.close(terminal)

    def stack_interrupted(self, *args: str, at: bytes) -> int:
        """Run `./stack <args>` and press Ctrl+C (SIGINT) when the output
        shows `at`, the question. Returns the exit code."""
        process = subprocess.Popen(self._command(args), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   cwd=self.path, env=_environment())
        try:
            _wait_for(process, at)
            return _interrupt(process)
        finally:
            process.kill()
            process.wait()

    @staticmethod
    def _command(args: tuple[str, ...]) -> list[str]:
        return [sys.executable, "-m", "stack", *args]


def _wait_for(process: subprocess.Popen, text: bytes) -> None:
    """Read the process's output until `text` shows; fail after RUN_SECONDS."""
    seen = b""
    deadline = time.monotonic() + RUN_SECONDS
    while text not in seen:
        left = deadline - time.monotonic()
        assert left > 0, f"the question never showed: {seen.decode()[-400:]}"
        ready, _, _ = select.select([process.stdout], [], [], left)
        if not ready:
            continue
        chunk = os.read(process.stdout.fileno(), 4096)
        assert chunk, f"the process ended first: {seen.decode()[-400:]}"
        seen += chunk


def _interrupt(process: subprocess.Popen) -> int:
    """Press Ctrl+C until the process ends, as a person does when the
    first press shows nothing; fail after RUN_SECONDS. Returns the exit
    code.

    One press is not enough on Linux. `input()` writes its prompt, then
    blocks in `read()` on standard input. A SIGINT that arrives between
    the two is handled before the read starts, so the read is never
    interrupted and the process waits on the open pipe for good: 20 of
    300 runs on Linux (python:3.14-slim), none of 300 on macOS. The next
    press interrupts the read."""
    deadline = time.monotonic() + RUN_SECONDS
    while True:
        process.send_signal(signal.SIGINT)
        try:
            return process.wait(timeout=PRESS_AGAIN_SECONDS)
        except subprocess.TimeoutExpired:
            assert time.monotonic() < deadline, "Ctrl+C never ended the process"


def _environment() -> dict[str, str]:
    """The test's environment with the repo's `lib` to import from, as
    `./stack` sets it."""
    return {**os.environ, "PYTHONPATH": str(REPO / "lib")}
