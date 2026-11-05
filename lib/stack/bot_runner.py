"""The bot-runner container as the stack's tools runtime.

The host-side `./stack` is stdlib-only by design: it starts fast and
needs no pip install before it can be run. But some commands are
thin wrappers over pipelines that want aiohttp, loguru, yaml and a
rendered service env. Rather than break the stdlib invariant on the host
or clone those pipelines in urllib, those commands `docker exec` into
`stack-core-bot-runner`, which already has every dependency and the env
pre-rendered, and run a stacklet's `bot/cli_entrypoint.py` there.

This module is the one way the host runs a command there (2026-11-05:
core and memory each kept a copy of it, which drifted apart). A caller
names the entry point; the functions differ in what the caller gets back:

    dispatch   the command's output streams to the terminal, its exit code ends the process
    report     progress streams to the terminal, stdout is a JSON report returned as a dict
    capture    everything captured, returned as (returncode, stdout, first stderr line)
    call       stdout returned, a failure raised, for a caller that records the outcome

`call_stacklet` is the same for any stacklet, an extension included: it
finds the stacklet's entry point by convention, wherever the runner
mounts it, and hands it the stacklet's own settings.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BOT_RUNNER_CONTAINER = "stack-core-bot-runner"

# A stacklet's commands for the bot runner, by convention (docs, memory).
ENTRYPOINT = "bot/cli_entrypoint.py"


def bot_runner_running() -> bool:
    """True when the bot-runner container is up. False if absent or stopped."""
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", BOT_RUNNER_CONTAINER],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def dispatch(entrypoint: str, *argv: str, stdin_bytes: bytes | None = None) -> dict:
    """Run `entrypoint <argv...>` inside the bot-runner.

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
        "python", entrypoint, *argv,
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


def report(entrypoint: str, command: str, *argv: str) -> dict:
    """Run `entrypoint <command> <argv...>` inside the bot-runner, for a
    command that answers with a JSON report.

    Its progress (stderr) streams to the terminal as it goes, since it can
    take minutes of model calls; its stdout is the report, returned as a
    dict for the caller to render. `{"error": ...}` when the runtime is
    unavailable, the command fails, or it answers without a report.
    """
    if not bot_runner_running():
        return {"error": f"{BOT_RUNNER_CONTAINER} is not running, bring core up first: stack up core"}
    cmd = ["docker", "exec", "-i", BOT_RUNNER_CONTAINER, "python", entrypoint, command, *argv]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
    except FileNotFoundError:
        return {"error": "docker CLI not found on this host"}
    if result.returncode != 0:
        return {"error": f"{command} failed (exit {result.returncode}), see above"}
    try:
        answer = json.loads(result.stdout)
    except json.JSONDecodeError:
        answer = None
    return answer if isinstance(answer, dict) else {"error": f"{command} answered without a report"}


def capture(entrypoint: str, command: str, *argv: str, timeout: int = 60,
            input_text: str | None = None) -> tuple[int, str, str]:
    """Run `entrypoint <command> <argv...>` inside the bot-runner and return
    `(returncode, stdout, reason)`, for a caller that uses the answer and
    must carry on without one.

    `stack memory search --nl` asks the container for keywords and then
    does its own work with them. A stopped container, a host with no
    docker and a command that timed out all come back as a nonzero code,
    because to that caller they mean the same thing: no answer, carry on.

    `reason` is the first line of the container's stderr, not all of it:
    an entry point that does not know the command prints its whole usage
    text, and a host whose bot runner was not restarted after an update
    would show that over the caller's own output. `input_text` goes to the
    command's stdin, for a payload too large or too structured for argv.
    """
    if not bot_runner_running():
        return 1, "", f"{BOT_RUNNER_CONTAINER} is not running"
    cmd = ["docker", "exec", "-i", BOT_RUNNER_CONTAINER, "python", entrypoint, command, *argv]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                input=input_text)
    except FileNotFoundError:
        return 1, "", "docker CLI not found on this host"
    except subprocess.TimeoutExpired:
        return 1, "", f"{command} timed out after {timeout}s"
    first_line = next((ln.strip() for ln in (result.stderr or "").splitlines() if ln.strip()), "")
    return result.returncode, result.stdout, first_line


def call(entrypoint: str, command: str, *argv: str, input_text: str | None = None,
         env: dict[str, str] | None = None) -> str:
    """Run `entrypoint <command> <argv...>` inside the bot-runner and return
    its stdout.

    For a caller that records the outcome rather than showing it, such
    as a migration: output is captured, and a failure raises
    RuntimeError with the last line the command wrote, instead of
    ending the process as `dispatch` does. `input_text` goes to the
    command's stdin, for input too large or too structured for argv.

    `env` is set for the command over the runner's own environment. Each
    key goes to `docker exec` as `-e KEY` with its value in the docker
    client's environment, which keeps values such as passwords out of
    every process's arguments.
    """
    if not bot_runner_running():
        raise RuntimeError(
            f"{BOT_RUNNER_CONTAINER} is not running, bring core up first: stack up core")
    env = env or {}
    result = subprocess.run(
        ["docker", "exec", *(["-i"] if input_text is not None else []),
         *[flag for key in sorted(env) for flag in ("-e", key)],
         BOT_RUNNER_CONTAINER, "python", entrypoint, command, *argv],
        capture_output=True, text=True, timeout=1200, input=input_text,
        env={**os.environ, **env} if env else None,
    )
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        reason = f": {lines[-1]}" if lines else ""
        raise RuntimeError(f"{command} in {BOT_RUNNER_CONTAINER} failed "
                           f"(exit {result.returncode}){reason}")
    return result.stdout


def call_stacklet(stacklet: dict, command: str, *argv: str,
                  input_text: str | None = None) -> str:
    """Run `stacklet`'s own entry point, `bot/cli_entrypoint.py <command>
    <argv...>`, inside the bot-runner with the stacklet's settings, and
    return its stdout. As `call` otherwise.

    `stacklet` is the stacklet a command is handed (`run(args, stacklet,
    config)`). Its entry point is where core mounts the stacklet
    (`runner_path`): the repo's under `/stacklets`, an extension's under
    `/extensions`. Its settings are its rendered `.env`, the settings
    `stack up` gave its containers, which the runner's own environment,
    core's, does not hold.

    Raises RuntimeError naming the fix when the runner cannot see the
    stacklet (an extension outside the first extension dir), or when the
    stacklet was never brought up, so nothing rendered its settings.
    """
    from . import env_file

    sid = stacklet["id"]
    if not stacklet.get("runner_path"):
        where = Path(stacklet["path"]).parent
        raise RuntimeError(
            f"{sid} is in {where}, which {BOT_RUNNER_CONTAINER} does not see: it mounts only "
            f"the first of [core] extension_dirs. Put {where} first there, then: stack restart core")
    rendered = Path(stacklet["path"]) / ".env"
    if not rendered.is_file():
        raise RuntimeError(f"{sid} has no settings yet: stack up {sid}")
    return call(f"{stacklet['runner_path']}/{ENTRYPOINT}", command, *argv,
                input_text=input_text, env=env_file.read(rendered))


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
