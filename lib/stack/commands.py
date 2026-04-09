"""CLI commands as classes.

Each command encapsulates one operation: execute() does the work,
format() presents the result. Commands receive a Stack instance —
they never access globals or create their own state.

The CLI dispatch becomes a simple registry lookup:
  command = COMMANDS[args.command]
  result = command.execute(stack, **kwargs)
  if pretty: print(command.format(result))

Commands only handle framework-level logic. Docker operations (compose
up/down, image pull, health checks) stay in the CLI layer and are
called separately.
"""

from .stack import Stack


class EnvCommand:
    """Render environment variables for a stacklet."""

    def execute(self, stack: Stack, stacklet: str) -> dict:
        try:
            return {"stacklet": stacklet, "env": stack.env(stacklet)}
        except ValueError as e:
            return {"error": str(e)}


class ListCommand:
    """Discover all stacklets and report their state."""

    def execute(self, stack: Stack) -> dict:
        return stack.list()


class UpCommand:
    """Bring a stacklet up: render env, run hooks, write .env.

    Docker operations (compose up, health check, image pull) are NOT
    handled here — they belong in the CLI layer which calls this command
    first, then orchestrates Docker.
    """

    def execute(self, stack: Stack, stacklet: str) -> dict:
        return stack.up(stacklet)


class DownCommand:
    """Stop a stacklet. Data preserved, hooks run."""

    def execute(self, stack: Stack, stacklet: str) -> dict:
        return stack.down(stacklet)


class DestroyCommand:
    """Remove a stacklet completely: hooks, secrets, data, markers."""

    def execute(self, stack: Stack, stacklet: str) -> dict:
        return stack.destroy(stacklet)


# ── Command registry ──────────────────────────────────────────────────────
#
# Maps command names to instances. The CLI uses this to dispatch.

COMMANDS = {
    "env": EnvCommand(),
    "list": ListCommand(),
    "up": UpCommand(),
    "down": DownCommand(),
    "destroy": DestroyCommand(),
}
