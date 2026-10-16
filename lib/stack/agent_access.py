"""`[agent] allow`: the stacklet commands the admin lets the agent run.

The agent reaches the CLI only through the core API's plaintext surface,
which runs a fixed set of memory, docs and speech commands. An admin
who wants it to operate something more lists that stacklet's commands
in stack.toml:

    [agent]
    allow = ["kettle"]                  # every command of the kettle stacklet
    allow = ["kettle status"]           # only this one

An entry is honoured only when its first word is a discovered stacklet
that has CLI commands and its second word, if any, is one of them. Further
words narrow the match, as the API matches leading words. So no entry can
name a framework command (`up`, `destroy`, `config`): those are not
stacklet commands. A stacklet whose id collides with one cannot shadow it
either, because the CLI refuses to build its parser at all then.

Two readers, one rule. The API asks `allowed_commands` on every request,
so a change to stack.toml applies without a restart. The agent hook calls
`write_command_skill` on `stack up agent`, which tells the agent what it
may run, each command with its own `HELP` line, and returns the entries it
refused so the hook can warn about them.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .plugins import plugin_help, stacklet_commands

# An always-loaded nanobot skill, so the agent sees the commands without
# spending a turn on `--help`. Sorts before `zz-cache-pad`, which has to
# stay last in the prompt.
SKILL_NAME = "stack-commands"


def configured_entries(stack) -> list[str]:
    """`[agent] allow` as written. A bare string is a one-entry list."""
    raw = stack.config.get("agent", {}).get("allow", [])
    if isinstance(raw, str):
        raw = [raw]
    return [str(entry) for entry in raw]


def _judge(stack) -> tuple[list[list[str]], list[str], dict]:
    """Split the configured entries into granted word lists and refused entries."""
    commands = stacklet_commands(stack)
    granted, refused = [], []
    for entry in configured_entries(stack):
        words = entry.split()
        stacklet = commands.get(words[0]) if words else None
        if stacklet and (len(words) == 1 or words[1] in stacklet):
            granted.append(words)
        else:
            refused.append(entry)
    return granted, refused, commands


def allowed_commands(stack) -> list[list[str]]:
    """The leading words of every command line the admin allowed."""
    return _judge(stack)[0]


# ── The skill the agent reads ────────────────────────────────────────────

def _command_lines(granted: list[list[str]], commands: dict) -> list[tuple[str, str]]:
    """`(stack <id> <cmd>, HELP)` per allowed command, each listed once."""
    lines: dict[str, str] = {}
    for words in granted:
        sid = words[0]
        names = [words[1]] if len(words) > 1 else list(commands[sid])
        for name in names:
            call = " ".join(["stack", sid, name, *words[2:]])
            lines.setdefault(call, plugin_help(commands[sid][name])[0] or "")
    return list(lines.items())


def _render_skill(lines: list[tuple[str, str]]) -> str:
    width = max(len(call) for call, _ in lines)
    table = "\n".join(f"  {call:<{width}}  {text}".rstrip() for call, text in lines)
    return (
        "---\n"
        f"name: {SKILL_NAME}\n"
        "description: The stack commands the admin lets me run.\n"
        'metadata: {"nanobot": {"always": true}}\n'
        "---\n"
        "# Stack commands\n\n"
        "Commands I can run with exec:\n\n"
        f"```\n{table}\n```\n"
    )


def write_command_skill(stack, workspace_dir: Path) -> list[str]:
    """Write the agent's command list into its workspace; return refused entries.

    With nothing allowed there is no skill directory at all, a stale one
    included, so the agent's prompt is the same as on an instance that
    never used `[agent] allow`.
    """
    granted, refused, commands = _judge(stack)
    skill_dir = Path(workspace_dir) / "skills" / SKILL_NAME
    if not granted:
        shutil.rmtree(skill_dir, ignore_errors=True)
        return refused
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(_render_skill(_command_lines(granted, commands)))
    return refused
