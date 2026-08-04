"""Docker-exec dispatcher for memory CLI commands that need LLM access.

The stack CLI plugin loader skips `_`-prefixed files, so this module
hosts shared plumbing without registering as a command.

Design: the memory stacklet's wiki command needs the LLM client +
aiohttp + frontmatter + the rendered AI env vars to run. The host-side
`./stack` is stdlib-only by design (fast startup, no pip install
needed). Rather than cloning that runtime on the host, host commands
docker-exec into the bot-runner container — it already has every dep
the archivist uses and the env is pre-rendered.

Mirrors `stacklets/docs/cli/_common.py`; both stacklets re-use the
bot-runner as their tools runtime.
"""

from __future__ import annotations

import subprocess
import sys

BOT_RUNNER_CONTAINER = "stack-core-bot-runner"
ENTRYPOINT_PATH = "/stacklets/memory/bot/cli_entrypoint.py"


def _bot_runner_running() -> bool:
    """True when the bot-runner container is up. False if absent or stopped."""
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", BOT_RUNNER_CONTAINER],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def dispatch(command: str, *argv: str) -> dict:
    """docker exec the bot-runner's memory entrypoint with the given args.

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
    # stderr diagnostic.
    if rc != 0:
        sys.exit(rc)
    return {"ok": True}


def dispatch_capture(command: str, *argv: str,
                     timeout: int = 60) -> tuple[int, str, str]:
    """The same hop, for a caller that wants the output as a value.

    `dispatch` is right when the container's output *is* the result:
    it streams to the terminal and exits with the container's status.
    `stack memory search --nl` is the other shape. It asks the
    container for keywords and then does its own work with them, so it
    needs them back, and it must stay alive when the hop is
    unavailable.

    Returns `(returncode, stdout, reason)`. A stopped container, a host
    with no docker, and a model that timed out all come back as a
    nonzero code, because to the caller they mean the same thing: no
    answer from here, carry on without one.

    `reason` is the first line of the container's stderr, and it is
    first line rather than all of it on purpose. An entry point that
    does not know the command prints its whole usage text, and a
    version-skewed host (updated code, bot-runner not restarted yet)
    would dump that over a family's search results. One line stays
    diagnostic without ever becoming a wall.
    """
    if not _bot_runner_running():
        return 1, "", f"{BOT_RUNNER_CONTAINER} is not running"

    cmd = [
        "docker", "exec", "-i",
        BOT_RUNNER_CONTAINER,
        "python", ENTRYPOINT_PATH, command, *argv,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        return 1, "", "docker CLI not found on this host"
    except subprocess.TimeoutExpired:
        return 1, "", f"{command} timed out after {timeout}s"

    first_line = next(
        (ln.strip() for ln in (result.stderr or "").splitlines() if ln.strip()),
        "",
    )
    return result.returncode, result.stdout, first_line
