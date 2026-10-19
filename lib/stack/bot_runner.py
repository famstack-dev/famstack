"""The bot-runner container as the stack's tools runtime.

The host-side `./stack` is stdlib-only by design: it starts fast and
needs no pip install before it can be run. But some commands are
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


def call(entrypoint: str, command: str, *argv: str) -> str:
    """Run `entrypoint <command> <argv...>` inside the bot-runner and return
    its stdout.

    For a caller that records the outcome rather than showing it, such
    as a migration: output is captured, and a failure raises
    RuntimeError with the last line the command wrote, instead of
    ending the process as `dispatch` does.
    """
    if not bot_runner_running():
        raise RuntimeError(
            f"{BOT_RUNNER_CONTAINER} is not running, bring core up first: stack up core")
    result = subprocess.run(
        ["docker", "exec", BOT_RUNNER_CONTAINER, "python", entrypoint, command, *argv],
        capture_output=True, text=True, timeout=1200,
    )
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        reason = f": {lines[-1]}" if lines else ""
        raise RuntimeError(f"{command} in {BOT_RUNNER_CONTAINER} failed "
                           f"(exit {result.returncode}){reason}")
    return result.stdout


def run_module(module: str, input_text: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run `python -m <module>` of the stack library inside the bot-runner.

    `input_text` goes to its stdin, which is how a credential reaches it
    without appearing in any process's arguments. Output is captured and
    returned as it is; the caller reads the module's own answer. Check
    `bot_runner_running` first for a message that names the fix.
    """
    return subprocess.run(
        ["docker", "exec", "-i", BOT_RUNNER_CONTAINER, "python", "-m", module],
        input=input_text, capture_output=True, text=True, timeout=timeout,
    )
