"""Agent runtime tool for exact household profile reads."""

from __future__ import annotations

import asyncio

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import StringSchema, tool_parameters_schema


@tool_parameters(
    tool_parameters_schema(
        name=StringSchema(
            "Person slug, canonical name, or synonym, such as homer or Marge Simpson.",
            min_length=1,
        ),
    )
)
class MemoryPersonTool(Tool):
    """Read a household member profile through the memory stacklet."""

    _scopes = {"core"}

    @property
    def name(self) -> str:
        return "memory_person"

    @property
    def description(self) -> str:
        return (
            "Read a household member's exact profile from the vault. Use this first "
            "for questions about the sender, identity, profile, or 'what do you know about me'."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, name: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "stack",
            "memory",
            "person",
            name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        if proc.returncode != 0:
            return f"Error: memory person failed with exit {proc.returncode}: {err or out}"
        return out or "(no profile found)"


def install() -> None:
    """Append MemoryPersonTool to nanobot discovery without forking nanobot."""
    from nanobot.agent.tools.loader import ToolLoader

    original = ToolLoader.discover

    def discover_with_person(self: ToolLoader) -> list[type[Tool]]:
        tools = list(original(self))
        if MemoryPersonTool not in tools:
            tools.append(MemoryPersonTool)
        return tools

    ToolLoader.discover = discover_with_person
