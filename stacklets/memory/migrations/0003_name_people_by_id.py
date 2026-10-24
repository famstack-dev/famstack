"""Name the people in every record by id and link their cards.

ADR-017 §3, §4. In each document, note, bookmark and email: a `persons`
entry a declared person goes by (`family/people/<id>.md`, by any name
they go by) becomes their id; each link in the meta block under the
title to a person's compiled page (`homer/about.md`) points at the
person's card instead; every `Person: <name>` tag goes. A link to a
document that moved since it was written points at where the document
is now, when its Paperless id (`...-p72.md`) names exactly one document.
Only those lines change; every other byte stays as it was.

Left as it is and named in the output: a name no declared person goes
by, with its link, and a link to a moved document no one document
answers. Diary cards are left alone: they are signed with a digest, and
an edit from outside the compile would make them the family's for good.
The migration stops, and runs again on the next `stack up memory`, while
no person files exist.

One commit by the memory bot, through the curator, each file written on
the version it has in the curator's clone, so an edit made meanwhile
makes the curator refuse it and nothing is written; the next `stack up
memory` reads again. From then on the archivist files records this way.
Undo: revert the commit in family/memory.
"""

from __future__ import annotations

from lib import (
    memory_for_migration,
    name_people_by_id,
)
from stack.vault import DEFAULT_SHARED_BUCKET

REPO = "family/memory"


def run(ctx):
    memory, vault = memory_for_migration(ctx)
    bucket = ctx.env.get("SHARED_BUCKET") or DEFAULT_SHARED_BUCKET
    found, commit = name_people_by_id(
        memory, vault,
        shared_bucket=bucket, trailer=ctx.trailer)
    for line in found.left:
        ctx.step(f"Memory: {line}; left as it is")
    if found.changes:
        ctx.step(f"Memory: named the people of {len(found.changes)} card(s) by id")
    return commit
