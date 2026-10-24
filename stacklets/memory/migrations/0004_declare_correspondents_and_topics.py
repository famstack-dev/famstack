"""Give every correspondent and every topic a card of its own.

ADR-017 §10. Each correspondent a document names gets
`family/correspondents/<slug>.md` (`type: correspondent`, `card_id`,
`title`), and each topic a record is filed into gets
`<bucket>/<topic>/topic.md` (`type: topic`, `card_id`, `title` read from
the folder's name). A card that is there is never rewritten. A
correspondent's file of the old shape (`kind: correspondent`, no `type`)
gets `type: correspondent` and its card id first in its frontmatter, and
nothing else changes in it. The aliases a classifier returned are not
on file for the documents filed before, so these cards start without
any, and a later document adds none: a card is written once. The family
adds aliases by hand.

One commit by the memory bot, through the curator, read from its clone:
a new card is written as a new file and a changed one on the version it
was read at, so a card that appeared or an edit made meanwhile makes the
curator refuse it and nothing is written; the next `stack up memory`
reads again. From then on the archivist writes these cards the first
time a record names them. Undo: revert the commit in family/memory.
"""

from __future__ import annotations

from lib import (
    declare_cards,
    memory_for_migration,
)
from stack.vault import DEFAULT_SHARED_BUCKET

REPO = "family/memory"


def run(ctx):
    memory, vault = memory_for_migration(ctx)
    bucket = ctx.env.get("SHARED_BUCKET") or DEFAULT_SHARED_BUCKET
    found, commit = declare_cards(
        memory, vault,
        shared_bucket=bucket, trailer=ctx.trailer)
    if found.changes:
        ctx.step(f"Memory: declared {len(found.created)} correspondent and topic card(s), "
                 f"typed {len(found.changes) - len(found.created)} old correspondent file(s)")
    return commit
