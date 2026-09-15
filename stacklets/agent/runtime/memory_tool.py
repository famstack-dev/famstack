"""Agent runtime tool for read-only family memory search."""

from __future__ import annotations

import asyncio
import re

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import (
    ArraySchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)


@tool_parameters(
    tool_parameters_schema(
        query=StringSchema(
            "Two to four literal keywords, words that appear on the page. "
            "Not a full question.",
            min_length=1,
        ),
        queries=ArraySchema(
            StringSchema("Keyword set for one search."),
            description="Up to three independent keyword sets, searched in "
                        "one call. Use instead of repeated search calls.",
            max_items=3,
            nullable=True,
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
        queries: list[str] | None = None,
        limit: int | None = None,
        scope: str | None = None,
        person: str | None = None,
        tag: str | None = None,
    ) -> str:
        # `queries` batches independent lookups into one tool call, so
        # one LLM iteration answers a question that needs two or three
        # searches. Each iteration costs a prompt prefill; the searches
        # themselves are cheap and run concurrently.
        batch = [q for q in (queries or []) if q and q.strip()] or [query]
        batch = batch[:3]
        results = await asyncio.gather(
            *(self._search_one(q, limit, scope, person, tag) for q in batch)
        )
        if len(batch) == 1:
            return results[0]
        return "\n\n".join(
            f"## {q}\n{r}" for q, r in zip(batch, results)
        )

    async def _search_one(
        self,
        query: str,
        limit: int | None,
        scope: str | None,
        person: str | None,
        tag: str | None,
    ) -> str:
        # The CLI's query language is a regex, and adjacent words match
        # nothing. Join the model's keywords with `|` so each keyword
        # matches on its own. This replaces the CLI's `--nl` rewrite,
        # which made a second LLM call inside every multi-word search
        # (measured 2026-09-15: one full model call per search, on the
        # same GPU as the turn). The model now supplies the keywords.
        words = [re.escape(w) for w in query.split()]
        pattern = "|".join(words) if len(words) > 1 else query
        args = [
            "stack",
            "memory",
            "search",
            pattern,
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
        # `stack memory search` exits 1 for "nothing matched", which is an
        # answer. Only 2 and up (bad arguments, unreadable vault) are
        # failures. Reporting an empty result as a failure tells the model
        # to try again when the honest reply is that there is nothing there.
        if proc.returncode not in (0, 1):
            return f"Error: memory search failed with exit {proc.returncode}: {err or out}"
        # The status decides, not the text. A search that matched nothing
        # reaches here as the API's generic "(no output)" placeholder, which
        # reads like something went wrong; the model's next move after an
        # ambiguous non-answer is to ask again.
        if proc.returncode == 1:
            return "(no memory results)"
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
