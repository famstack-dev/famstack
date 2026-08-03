"""The bot-runner container as the stack's tools runtime.

The host-side `./stack` is stdlib-only by design: it starts fast and
needs no pip install before a family can use it. But some commands are
thin wrappers over pipelines that want aiohttp, loguru, yaml and a
rendered service env. Rather than break the stdlib invariant on the host
or clone those pipelines in urllib, those commands `docker exec` into
`stack-core-bot-runner`, which already has every dependency and the env
pre-rendered, and run a stacklet's `bot/cli_entrypoint.py` there.

`stacklets/docs/cli/_common.py` established the pattern and its docstring
predicted it would generalise. It did, at `stack memory capture`, so the
mechanism lives here rather than being copied per stacklet. A caller
supplies its own entrypoint path; nothing else differs between them.
"""

from __future__ import annotations

import subprocess
import sys

BOT_RUNNER_CONTAINER = "stack-core-bot-runner"


def bot_runner_running() -> bool:
    """True when the bot-runner container is up. False if absent or stopped."""
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", BOT_RUNNER_CONTAINER],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def dispatch(entrypoint: str, command: str, *argv: str,
             stdin_bytes: bytes | None = None) -> dict:
    """Run `entrypoint <command> <argv...>` inside the bot-runner.

    Returns `{"ok": True}` on success, `{"error": ...}` when the runtime
    is unavailable. stdout and stderr stream straight through so the
    caller sees live output; when the host is a TTY the exec gets one
    too, so ANSI colors from stack.prompt render correctly.

    `stdin_bytes` feeds the container's stdin, which is how a host file
    reaches a pipeline running inside a container that cannot see the
    host filesystem. Piped input and an allocated TTY are mutually
    exclusive, so supplying bytes drops the TTY.
    """
    if not bot_runner_running():
        return {"error": f"{BOT_RUNNER_CONTAINER} is not running — bring core up first: stack up core"}

    piping = stdin_bytes is not None
    tty_flags = ["-it"] if (sys.stdout.isatty() and not piping) else ["-i"]
    cmd = [
        "docker", "exec", *tty_flags,
        BOT_RUNNER_CONTAINER,
        "python", entrypoint, command, *argv,
    ]
    try:
        rc = (subprocess.run(cmd, input=stdin_bytes).returncode if piping
              else subprocess.call(cmd))
    except FileNotFoundError:
        return {"error": "docker CLI not found on this host"}

    # Pass rc through to the shell without letting the harness print a
    # generic "command failed (exit N)" on top of the container's own
    # stderr diagnostic. sys.exit bypasses the {"error": ...} path, so
    # scripts still see the right return code without the double message.
    if rc != 0:
        sys.exit(rc)
    return {"ok": True}
