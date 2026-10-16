"""Move each topic's old todos.md to the family's lists.

`<bucket>/<topic>/todos.md` becomes `<bucket>/lists/<topic>.md`, in one
commit by the memory bot. A topic that has a list already keeps it and
gains the old file's items that are not on it. Each file is written
against the sha it was read at, so an edit made meanwhile makes Forgejo
refuse the commit and nothing is written; the next `stack up memory`
reads again. Undo: revert the commit in family/memory.
"""

from __future__ import annotations

from lib import host_code_url, move_todos_to_lists
from stack.forgejo import ForgejoClient
from stack.vault import DEFAULT_SHARED_BUCKET

REPO = "family/memory"


def run(ctx):
    code_url = host_code_url(ctx.env.get("CODE_URL", ""))
    token = ctx.secret("MEMORY_BOT_TOKEN")
    if not (code_url and token):
        raise RuntimeError("no Forgejo address or memory write token on file")
    bucket = ctx.env.get("SHARED_BUCKET") or DEFAULT_SHARED_BUCKET
    moved, commit = move_todos_to_lists(
        ForgejoClient(url=code_url, token=token), shared_bucket=bucket,
        language=ctx.env.get("LANGUAGE", ""), trailer=ctx.trailer)
    if moved:
        ctx.step(f"Memory: moved the todo lists of {', '.join(moved)} to {bucket}/lists/")
    return commit
