"""Give every card in the family memory an id of its own, `card_id`.

A card without one gets `<type>-<value>` (ADR-016): a document, note,
bookmark, email or diary entry ten random hex digits, a person or a list
its file's slug, a list's archive the list's slug and month. A file with
no frontmatter or no `type` was written by hand and becomes a note: it
gets `type: note`, a `title` from its first heading or else its file
name, and an id. `README.md`, `index.md` and pages the wiki compiled
(views, `stack.cards.is_view`) are no cards and are left alone. Of two files with
one id, the one first committed later gets a new id. Only those lines
are added or rewritten; every other byte stays as it was. A file the
rule cannot place (frontmatter that does not parse, a `type` that is no
card type, a `kind` with no `type`) is left as it is and named in the
output.

One commit by the memory bot, through the curator, each file written on
the version it has in the curator's clone, so an edit made meanwhile
makes the curator refuse it and nothing is written; the next `stack up
memory` reads again. From then on the writers give new cards their id
and the curator gives one to files written by hand. Undo: revert the
commit in family/memory.
"""

from __future__ import annotations

from lib import (
    give_card_ids,
    memory_for_migration,
)

REPO = "family/memory"


def run(ctx):
    memory, vault = memory_for_migration(ctx)
    found, commit = give_card_ids(
        memory, vault, trailer=ctx.trailer)
    for line in found.left:
        ctx.step(f"Memory: no card id for {line}; left as it is")
    if found.changes:
        ctx.step(f"Memory: gave {len(found.changes)} card(s) an id of their own")
    return commit
