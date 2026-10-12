"""Drop unused nanobot tools from the model's tool list.

nanobot 0.3.5 has no config gate for these tools. They enable with the
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
    "cron",                  # agent-set reminders, not used yet
    "create_goal",           # long-running goals, not used
    "update_goal",           # same group
    "spawn",                 # subagents, not used
    "exec_session",          # interactive exec sessions, agent runs one-shot CLI
    "list_exec_sessions",    # same group
    # Other conversations. A session is a room or a thread, and some are
    # one person's: reading or writing one from another room would carry
    # what was said there to people who were not in it.
    "list_sessions",
    "read_session",
    "search_sessions",
    "send_session_message",
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
