"""Route vault grep calls through semantic family memory search."""

from __future__ import annotations

import re
from typing import Any

_PATH_RE = re.compile(r"^\s*#\d+\s+.*?\s([^\s]+\.md)\s+score=", re.MULTILINE)


def _is_vault_path(path: str | None) -> bool:
    path = (path or ".").strip().replace("\\", "/")
    return path in {"vault", "./vault"} or path.startswith(("vault/", "./vault/"))


def install() -> None:
    """Patch nanobot's grep tool so vault searches use memory_search."""
    from nanobot.agent.tools.search import GrepTool

    original = GrepTool.execute

    async def execute_with_memory(
        self: GrepTool,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        type: str | None = None,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        output_mode: str = "files_with_matches",
        context_before: int = 0,
        context_after: int = 0,
        max_matches: int | None = None,
        max_results: int | None = None,
        head_limit: int | None = None,
        offset: int = 0,
        **kwargs: Any,
    ) -> str:
        if not _is_vault_path(path):
            return await original(
                self,
                pattern=pattern,
                path=path,
                glob=glob,
                type=type,
                case_insensitive=case_insensitive,
                fixed_strings=fixed_strings,
                output_mode=output_mode,
                context_before=context_before,
                context_after=context_after,
                max_matches=max_matches,
                max_results=max_results,
                head_limit=head_limit,
                offset=offset,
                **kwargs,
            )

        limit = head_limit or max_results or max_matches or 10
        if limit == 0:
            limit = 20

        scope = None
        normalized = path.strip().replace("\\", "/").removeprefix("./")
        if normalized.startswith("vault/"):
            scope = normalized.removeprefix("vault/").strip("/") or None

        from memory_tool import MemorySearchTool

        result = await MemorySearchTool().execute(
            query=pattern,
            limit=min(max(int(limit), 1), 20),
            scope=scope,
        )
        paths = [f"vault/{path}" for path in _PATH_RE.findall(result)]
        path_block = ""
        if paths:
            path_block = "Paths to read:\n" + "\n".join(f"- {path}" for path in paths) + "\n\n"
        return (
            "Semantic vault search via memory_search. "
            "Use returned vault paths with read_file for source verification.\n\n"
            + path_block
            + result
        )

    GrepTool.execute = execute_with_memory
