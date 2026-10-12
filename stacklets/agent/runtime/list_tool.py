"""Agent runtime tool for item-level list edits.

Free-form page editing loses state on restructures (measured
2026-09-15: a category rewrite unticked a done item). For item
operations the model only names the item; the store matches it and
preserves everything else by construction. One route per intent:
`list_edit` for items, `write_file` for restructures. The old
structured CLI verbs failed because two routes competed for one
intent (docs/design/agent/todo-strike-flaky.md); this tool is the
only documented item route.
"""

from __future__ import annotations

import asyncio

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import (
    ArraySchema,
    StringSchema,
    tool_parameters_schema,
)


@tool_parameters(
    tool_parameters_schema(
        list=StringSchema(
            "The list's name, such as einkaufsliste. `stack memory list "
            "show` names every list with its description.",
            min_length=1,
        ),
        op=StringSchema(
            "Item operation: add, tick, untick, remove, or move to another "
            "list. reset reopens every ticked item.",
            enum=("add", "tick", "untick", "remove", "move", "reset"),
        ),
        items=ArraySchema(
            StringSchema("One item's text, as it appears on the list."),
            description="The item(s) for this op, one or more, in the "
                        "family's words. Omit for reset.",
            nullable=True,
        ),
        section=StringSchema(
            "For add: one of the list's own sections, such as "
            "Milchprodukte & Eier.",
            nullable=True,
        ),
        people=ArraySchema(
            StringSchema("A family member's name."),
            description="For add: who the item is for, only when someone "
                        "says so.",
            nullable=True,
        ),
        due=StringSchema("For add: the day it is due, YYYY-MM-DD.", nullable=True),
        to=StringSchema("For move: the list the items go to.", nullable=True),
    )
)
class ListEditTool(Tool):
    """Change items on a family list."""

    _scopes = {"core"}

    @property
    def name(self) -> str:
        return "list_edit"

    @property
    def description(self) -> str:
        return (
            "Add, tick, untick, remove or move items on a family list, by "
            "name. The store matches each item and answers with what "
            "actually changed. Use this for every item change; use "
            "write_file only to restructure a whole list."
        )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(
        self,
        list: str,
        op: str,
        items: "list[str] | None" = None,
        section: str | None = None,
        people: "list[str] | None" = None,
        due: str | None = None,
        to: str | None = None,
    ) -> str:
        try:
            import brief
            actor = getattr(brief, "speaking_with", "") or "someone"
        except Exception:
            actor = "someone"

        args = ["stack", "memory", "list", op, list.strip()]
        if op != "reset":
            args += [i for i in (items or []) if i and i.strip()]
        if op == "add":
            if section:
                args += ["--section", section]
            for person in people or []:
                args += ["--for", person]
            if due:
                args += ["--due", due]
        if op == "move" and to:
            args += ["--to", to]
        args += ["--as", actor]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        # Exit 1 carries an instructive answer (ambiguous item, a section or
        # list that does not exist, with the choices). The model reads it
        # and retries once.
        if proc.returncode not in (0, 1):
            return f"Error: list edit failed with exit {proc.returncode}: {err or out}"
        return out or err or "(no answer from the list store)"


def install() -> None:
    """Append ListEditTool to nanobot discovery, same pattern as memory_tool."""
    from nanobot.agent.tools.loader import ToolLoader

    original = ToolLoader.discover

    def discover_with_list_edit(self: ToolLoader) -> list[type[Tool]]:
        tools = list(original(self))
        if ListEditTool not in tools:
            tools.append(ListEditTool)
        return tools

    ToolLoader.discover = discover_with_list_edit
