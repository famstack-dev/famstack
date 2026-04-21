"""Docker-exec dispatcher for docs CLI commands.

The stack CLI plugin loader skips `_`-prefixed files, so this module
hosts shared plumbing without registering as a command.

Design: the archivist pipeline needs aiohttp + loguru + the rendered
Paperless/AI env vars to run. The host-side `./stack` is stdlib-only by
design (fast startup, no pip install needed). Rather than cloning the
pipeline in urllib or breaking the stdlib invariant, host commands
docker-exec into the bot-runner container — it already has every dep
the archivist uses and the env is pre-rendered.

The same pattern generalises: any stacklet CLI that needs non-stdlib
deps can grow a sibling `bot/cli_entrypoint.py` and a thin host
dispatcher here. Keeps the host wrapper minimal and reuses the
container's Python environment as the stack's "tools runtime".
"""

from __future__ import annotations

import subprocess
import sys

BOT_RUNNER_CONTAINER = "stack-core-bot-runner"
ENTRYPOINT_PATH = "/stacklets/docs/bot/cli_entrypoint.py"


def _bot_runner_running() -> bool:
    """True when the bot-runner container is up. False if absent or stopped."""
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", BOT_RUNNER_CONTAINER],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def dispatch(command: str, *argv: str) -> dict:
    """docker exec the bot-runner's cli_entrypoint with the given args.

    Returns `{"ok": True}` on success, `{"error": ...}` on failure. stdout
    and stderr stream straight through so the caller sees live output.
    When the host is a TTY the exec is allocated one too, so ANSI colors
    from stack.prompt render correctly.
    """
    if not _bot_runner_running():
        return {"error": f"{BOT_RUNNER_CONTAINER} is not running — bring core up first: stack up core"}

    tty_flags = ["-it"] if sys.stdout.isatty() else ["-i"]
    cmd = [
        "docker", "exec", *tty_flags,
        BOT_RUNNER_CONTAINER,
        "python", ENTRYPOINT_PATH, command, *argv,
    ]
    try:
        rc = subprocess.call(cmd)
    except FileNotFoundError:
        return {"error": "docker CLI not found on this host"}

    # Pass rc through to the shell without letting the harness print a
    # generic "command failed (exit N)" on top of the container's own
    # stderr diagnostic. sys.exit bypasses the {"error": ...} path, so
    # scripts still see the right return code without the double message.
    if rc != 0:
        sys.exit(rc)
    return {"ok": True}
