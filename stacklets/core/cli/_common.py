"""Docker-exec dispatcher for core CLI commands that run in the bot-runner.

The stack CLI plugin loader skips `_`-prefixed files, so this hosts shared
plumbing without registering as a command. Core host commands stay
stdlib-only (fast startup) and docker-exec into stack-core-bot-runner, which
already has `stack.mail_fetcher` and the rendered mail env.

Mirrors `stacklets/memory/cli/_common.py`.
"""

from __future__ import annotations

import subprocess
import sys

BOT_RUNNER_CONTAINER = "stack-core-bot-runner"


def _bot_runner_running() -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", BOT_RUNNER_CONTAINER],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def dispatch(script_path: str, *argv: str) -> dict:
    """docker exec a python script inside the bot-runner with the given args.

    Streams stdout/stderr straight through; allocates a TTY when the host has
    one so colors render. Returns `{"ok": True}` or `{"error": ...}`; a
    non-zero exit from the container propagates as this process's exit code.
    """
    if not _bot_runner_running():
        return {"error": f"{BOT_RUNNER_CONTAINER} is not running — bring core up first: stack up core"}

    tty_flags = ["-it"] if sys.stdout.isatty() else ["-i"]
    cmd = ["docker", "exec", *tty_flags, BOT_RUNNER_CONTAINER, "python", script_path, *argv]
    try:
        rc = subprocess.call(cmd)
    except FileNotFoundError:
        return {"error": "docker CLI not found on this host"}

    if rc != 0:
        sys.exit(rc)
    return {"ok": True}
