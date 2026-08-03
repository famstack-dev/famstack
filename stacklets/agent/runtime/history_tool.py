"""Agent runtime tool for reading what changed in the vault, and when.

The vault is a git repository, so it already knows every version of every
page and who wrote it. `stack memory history` reads that back; this makes it
a tool the model can actually see.

WHY A TOOL AND NOT A LINE IN THE SKILL
    It was a line in the skill first, and the model ignored it. Asked what
    Homer had been up to lately, it called `memory_search` four times with
    progressively vaguer queries and never once ran the command that answers
    the question directly. Registered tools are what a model chooses between;
    prose describing a shell command is something it has to remember to
    remember, and under a concrete question it reaches for the tool it can
    see. `memory_search` and `memory_person` are tools for the same reason.

WHY SEARCH CANNOT COVER THIS
    Search ranks pages by what they say now. "Lately", "since when", "who
    changed this" are questions about the difference between versions, which
    no amount of searching the current text can answer -- and the failure is
    silent, because a plausible page always comes back.
"""

from __future__ import annotations

import asyncio

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import (
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)


@tool_parameters(
    tool_parameters_schema(
        scope=StringSchema(
            "Optional. A topic or person to limit this to, as the family would "
            "say it: camping, homer.",
            nullable=True,
        ),
        by=StringSchema(
            "Optional. Only changes made by this person.",
            nullable=True,
        ),
        since=StringSchema(
            "Optional. How far back, in plain words: 'last week', '3 days ago', "
            "'2026-07-01'.",
            nullable=True,
        ),
        item=StringSchema(
            "Optional. Find when this exact text first appeared in the vault, "
            "and who added it. Use the family's own wording.",
            nullable=True,
        ),
        limit=IntegerSchema(
            "Optional. How many changes to return (default 10).",
            nullable=True,
        ),
    )
)
class MemoryHistoryTool(Tool):
    """Read the vault's own history through the memory stacklet."""

    _scopes = {"core"}

    @property
    def name(self) -> str:
        return "memory_history"

    @property
    def description(self) -> str:
        return (
            "What changed in the family's memory, and when. Use this for any "
            "question with time in it: what someone has been up to lately, what "
            "is new this week, who changed a page, or when something was added. "
            "Pass item to find when a specific line first appeared and who added "
            "it. Searching only sees what pages say now, so it cannot answer "
            "these."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, scope: str | None = None, by: str | None = None,
                      since: str | None = None, item: str | None = None,
                      limit: int | None = None) -> str:
        argv = ["stack", "memory", "history"]
        if scope:
            argv.append(str(scope))
        for flag, value in (("--item", item), ("--by", by),
                            ("--since", since), ("--limit", limit)):
            if value:
                argv += [flag, str(value)]

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        if proc.returncode != 0:
            return f"Error: memory history failed with exit {proc.returncode}: {err or out}"
        return out or "(no changes found)"


def install() -> None:
    """Append MemoryHistoryTool to nanobot discovery without forking nanobot."""
    from nanobot.agent.tools.loader import ToolLoader

    original = ToolLoader.discover

    def discover_with_history(self: ToolLoader) -> list[type[Tool]]:
        tools = list(original(self))
        if MemoryHistoryTool not in tools:
            tools.append(MemoryHistoryTool)
        return tools

    ToolLoader.discover = discover_with_history
