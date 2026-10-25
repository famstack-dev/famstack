"""Move each topic's old todos.md to the family's lists.

`<bucket>/<topic>/todos.md` becomes `<bucket>/lists/<topic>.md`, in one
commit by the memory bot. A topic that has a list already keeps it and
gains the old file's items that are not on it. The commit goes through
the curator, each file on the version it has in the curator's clone, so
an edit made meanwhile makes the curator refuse the commit and nothing
is written; the next `stack up memory` reads again. Undo: revert the
commit in family/memory.
"""

from __future__ import annotations

from lib import memory_for_migration, move_todos_to_lists
from stack.vault import DEFAULT_SHARED_BUCKET

REPO = "family/memory"


def run(ctx):
    memory, vault = memory_for_migration(ctx)
    bucket = ctx.env.get("SHARED_BUCKET") or DEFAULT_SHARED_BUCKET
    moved, commit = move_todos_to_lists(
        memory, vault, shared_bucket=bucket,
        language=ctx.env.get("LANGUAGE", ""), trailer=ctx.trailer)
    if moved:
        ctx.step(f"Memory: moved the todo lists of {', '.join(moved)} to {bucket}/lists/")
    return commit
