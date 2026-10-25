"""Give list archives and diary cards their kind.

ADR-017 §6: a card's `kind` is one of its type's fixed set. Every list
archive (`<bucket>/lists/archive/<slug>/<YYYY-MM>.md`) gets
`kind: archive`, so a calendar's archive no longer reads as a to-do list
with its past appointments open. Every diary card's `medium` line becomes
its `kind` line, where it is; a card the diary compile still owns is
signed again and stays the compile's, one a person changed stays theirs.
A `kind` that only repeats a type that has none (`type: correspondent`
with `kind: correspondent`, from before types) goes. Only those lines
change; every other byte stays as it was. A `kind` outside its type's set
that the rule cannot place is left as it is and named in the output.

One commit by the memory bot, through the curator, each file written on
the version it has in the curator's clone, so an edit made meanwhile
makes the curator refuse it and nothing is written; the next `stack up
memory` reads again. From then on the nightly move and the diary compile
write the kind. Undo: revert the commit in family/memory.
"""

from __future__ import annotations

from ..lib import (
    give_card_kinds,
    memory_for_migration,
)

REPO = "family/memory"


def run(ctx):
    memory, vault = memory_for_migration(ctx)
    found, commit = give_card_kinds(
        memory, vault, trailer=ctx.trailer)
    for line in found.left:
        ctx.step(f"Memory: {line}; left as it is")
    if found.changes:
        ctx.step(f"Memory: gave {len(found.changes)} card(s) their kind")
    return commit
