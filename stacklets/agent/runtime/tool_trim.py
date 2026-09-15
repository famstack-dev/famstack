"""Drop unused nanobot tools from the model's tool list.

nanobot 0.2.2 has no config gate for these tools. They enable with the
gateway services and put their JSON schemas into every request. The
family agent does not use them. Fewer schemas make a shorter and more
stable prompt prefix.

Set AGENT_TOOL_TRIM=0 to keep the full tool list.
"""

from __future__ import annotations

import os

# Tool names, as the model sees them. `message` stays: the gateway can
# send mid-turn channel messages through it.
TRIMMED = {
    "cron",                # agent-set reminders, not used yet
    "long_task",           # background goal loop, not used
    "complete_goal",       # pairs with long_task
    "spawn",               # subagents, not used
    "write_stdin",         # interactive exec sessions, agent runs one-shot CLI
    "list_exec_sessions",  # same group
}


def install() -> None:
    """Filter tool registration. The registry is the one seam every
    tool passes through, so class names stay out of it."""
    if os.environ.get("AGENT_TOOL_TRIM", "1") == "0":
        return
    from nanobot.agent.tools.registry import ToolRegistry

    original = ToolRegistry.register

    def register_trimmed(self: ToolRegistry, tool) -> None:
        if getattr(tool, "name", None) in TRIMMED:
            return
        original(self, tool)

    ToolRegistry.register = register_trimmed
