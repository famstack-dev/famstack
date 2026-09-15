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
from nanobot.agent.tools.schema import StringSchema, tool_parameters_schema


@tool_parameters(
    tool_parameters_schema(
        page=StringSchema(
            "The list page, such as vault/family/groceries/todos.md.",
            min_length=1,
        ),
        op=StringSchema(
            "Item operation, or bulk: clear-done removes all ticked "
            "items, reset reopens them.",
            enum=("add", "tick", "untick", "remove", "clear-done", "reset"),
        ),
        item=StringSchema(
            "The item text, as it appears on the list (for add: the new "
            "item, in the family's words). Empty for bulk operations.",
            nullable=True,
        ),
        section=StringSchema(
            "Optional section heading for add, such as Dairy.",
            nullable=True,
        ),
    )
)
class ListEditTool(Tool):
    """Change one item on a family list."""

    _scopes = {"core"}

    @property
    def name(self) -> str:
        return "list_edit"

    @property
    def description(self) -> str:
        return (
            "Add, tick, untick, or remove ONE item on a family list page. "
            "The store matches the item and answers with what actually "
            "changed. Use this for every item change; use write_file only "
            "to restructure a whole list."
        )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(
        self,
        page: str,
        op: str,
        item: str | None = None,
        section: str | None = None,
    ) -> str:
        target = page.strip()
        for marker in ("workspace/vault/", "vault/"):
            if marker in target:
                target = target.split(marker, 1)[1]
                break
        try:
            import brief
            actor = getattr(brief, "speaking_with", "") or "someone"
        except Exception:
            actor = "someone"

        args = ["stack", "memory", "list-edit", target,
                "--op", op, "--item", item or "", "--by", actor]
        if section:
            args.extend(["--section", section])

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        # Exit 1 carries an instructive answer (ambiguous item, unknown
        # item with candidates). The model reads it and retries once.
        if proc.returncode not in (0, 1):
            return f"Error: list edit failed with exit {proc.returncode}: {err or out}"
        return out or "(no answer from the list store)"


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
