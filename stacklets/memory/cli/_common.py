"""The memory's commands that run in the bot runner.

The stack CLI plugin loader skips `_`-prefixed files, so this module
hosts shared plumbing without registering as a command.

Some memory commands need the LLM client and the rendered AI settings,
which the stdlib-only host does not have. They run the memory's entry
point in the bot runner through `stack.bot_runner`, the one way the host
does that. These wrappers only name the entry point; the commands import
them by these names, and the tests replace them there.
"""

from __future__ import annotations

from stack import bot_runner

# Where core mounts the memory stacklet in the bot runner, plus the
# convention's entry point.
_ENTRYPOINT = f"/stacklets/memory/{bot_runner.ENTRYPOINT}"


def dispatch(command: str, *argv: str) -> dict:
    """Run a memory command in the bot runner, its output on the terminal.
    As `stack.bot_runner.dispatch`."""
    return bot_runner.dispatch(_ENTRYPOINT, command, *argv)


def dispatch_report(command: str, *argv: str) -> dict:
    """Run a memory command that answers with a JSON report.
    As `stack.bot_runner.report`."""
    return bot_runner.report(_ENTRYPOINT, command, *argv)


def dispatch_capture(command: str, *argv: str, timeout: int = 60,
                     input_text: str | None = None) -> tuple[int, str, str]:
    """Run a memory command and return `(returncode, stdout, reason)`.
    As `stack.bot_runner.capture`."""
    return bot_runner.capture(_ENTRYPOINT, command, *argv, timeout=timeout,
                              input_text=input_text)
