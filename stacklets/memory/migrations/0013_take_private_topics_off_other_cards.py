"""Take a person's private topic id off every card outside that person's bucket.

A topic card in a person's bucket (`marge/surprise-party/topic.md`) is
that person's private topic. Migrations memory/0005 and memory/0011, and
memory/0010 for the diary, tagged every card against the topics of every
bucket, so a family card that said "Homers 40th" got `surprise-party`,
the id of Marge's topic, and showed it to Homer.

What it reads: every card in the curator's clone, the topic cards of
every bucket, and the history of each card that carries such an id.
What it writes: the card's `tags` without that id, every other byte as
it was (`stacklets.memory.place_tags`, `stack.filing.withheld`). A card in the topic's own bucket
is not changed. A tag a person added by hand stays, and so does one on
a card whose history cannot be read: both are named in the output, for
the admin. A diary card the compile owns is signed again.

One commit by the memory bot, through the curator, each card written on
the version the pass read, so an edit made meanwhile makes the curator
refuse it and nothing is written; the next `stack up memory` reads
again. A second run finds nothing to change. Undo: revert the commit in
family/memory.

It reaches the curator through the memory's lib and `place_tags`, so it
imports the memory's domain from the host, as every memory migration does.
"""

from __future__ import annotations

from ..lib import DEFAULT_SHARED_BUCKET, memory_for_migration
from ..place_tags import repair_private_topic_tags


def run(ctx):
    memory, vault = memory_for_migration(ctx)
    found, commit = repair_private_topic_tags(
        memory, vault, shared_bucket=ctx.env.get("SHARED_BUCKET") or DEFAULT_SHARED_BUCKET,
        trailer=ctx.trailer)
    for line in found.left:
        ctx.step(f"Memory: {line}; left as it is")
    if found.changes:
        ctx.step(f"Memory: took another person's topic off {len(found.changes)} card(s)")
    return commit
