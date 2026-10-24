"""Give correspondents written by hand their title.

ADR-017 §5: a card's name is its `title`. Before correspondents were
cards, the memory stacklet documented a shape for a correspondent's file
written by hand that named it in `canonical`, and households that
followed it have such files; migration 0004 gave them a `type` and a
`card_id`, and kept the rest. Each correspondent with a `canonical` and
no title gets its `canonical` line as its `title` line, where it is, the
name as written. One that has a title loses `canonical`, and a name
there that is neither its title nor one of its aliases is kept as an
alias and named in the output. Only those lines change; `address`,
`website`, `phone`, `email` and `topics` are fields of the card and stay.

One commit by the memory bot, through the curator, each file written on
the version it has in the curator's clone, so an edit made meanwhile
makes the curator refuse it and nothing is written; the next `stack up
memory` reads again. A second run finds no `canonical` left. Undo:
revert the commit in family/memory.
"""

from __future__ import annotations

from lib import (
    memory_for_migration,
    title_correspondents,
)


def run(ctx):
    memory, vault = memory_for_migration(ctx)
    found, commit = title_correspondents(
        memory, vault, trailer=ctx.trailer)
    for line in found.left:
        ctx.step(f"Memory: {line}")
    if found.changes:
        ctx.step(f"Memory: gave {len(found.changes)} correspondent(s) written by hand their title")
    return commit
