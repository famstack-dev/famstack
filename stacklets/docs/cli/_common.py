"""Docker-exec dispatcher for docs CLI commands.

The stack CLI plugin loader skips `_`-prefixed files, so this module
hosts shared plumbing without registering as a command.

The mechanism itself now lives in `stack.bot_runner`, lifted there when
`stack memory capture` became its second user. This module keeps the
docs entrypoint path and its own name so command modules read unchanged.
"""

from __future__ import annotations

from stack.bot_runner import BOT_RUNNER_CONTAINER, bot_runner_running  # noqa: F401
from stack.bot_runner import dispatch as _dispatch

ENTRYPOINT_PATH = "/stacklets/docs/bot/cli_entrypoint.py"


def dispatch(command: str, *argv: str) -> dict:
    """docker exec the docs bot's cli_entrypoint with the given args."""
    return _dispatch(ENTRYPOINT_PATH, command, *argv)
