"""Agent runtime tool for read-only family memory search."""

from __future__ import annotations

import asyncio

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema


@tool_parameters(
    tool_parameters_schema(
        query=StringSchema(
            "Natural-language question or keywords to search across the family vault.",
            min_length=1,
        ),
        limit=IntegerSchema(
            5,
            description="Maximum number of results to return.",
            minimum=1,
            maximum=20,
            nullable=True,
        ),
        scope=StringSchema(
            "Optional vault scope, such as family/itchy-scratchy-land. Leave empty for global search.",
            nullable=True,
        ),
        person=StringSchema(
            "Optional person filter, such as lisa or homer.",
            nullable=True,
        ),
        tag=StringSchema(
            "Optional tag filter.",
            nullable=True,
        ),
    )
)
class MemorySearchTool(Tool):
    """Search the family vault through the memory stacklet."""

    _scopes = {"core"}

    @property
    def name(self) -> str:
        return "memory_search"

    @property
    def description(self) -> str:
        return (
            "Search the family memory vault. Results include rank, score, vault path, "
            "snippet, and source links when available. Use before answering factual "
            "questions about family people, plans, documents, notes, bookmarks, or topics."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        query: str,
        limit: int | None = None,
        scope: str | None = None,
        person: str | None = None,
        tag: str | None = None,
    ) -> str:
        args = [
            "stack",
            "memory",
            "search",
            query,
            "--limit",
            str(limit or 5),
        ]
        for flag, value in (("--scope", scope), ("--person", person), ("--tag", tag)):
            if value:
                args.extend([flag, value])

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=130)
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        if proc.returncode != 0:
            return f"Error: memory search failed with exit {proc.returncode}: {err or out}"
        return out or "(no memory results)"


def install() -> None:
    """Append MemorySearchTool to nanobot discovery without forking nanobot."""
    from nanobot.agent.tools.loader import ToolLoader

    original = ToolLoader.discover

    def discover_with_memory(self: ToolLoader) -> list[type[Tool]]:
        tools = list(original(self))
        if MemorySearchTool not in tools:
            tools.append(MemorySearchTool)
        return tools

    ToolLoader.discover = discover_with_memory
