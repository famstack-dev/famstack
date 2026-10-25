"""Tag every card's topics by the ontology's id, its keywords lowercase with hyphens.

ADR-017 §4. In the `tags` of every card but the diary's: a tag that names
a topic of the memory's ontology (`ontology.toml`), by its id or by its
label in any of the ontology's languages, in any case and spacing,
becomes the topic's id (`Gesundheit` and `Medical` become `medical`).
Every other tag is lowercased with hyphens between its words
(`Cropper Paper` becomes `cropper-paper`). Two tags that become one are
kept once, in the place of the first; the others keep their order. Only
the `tags` field changes; every other byte stays as it was.

Left as it is and named in the output: a tag that is no keyword even
then (`Moe's`, `2026`), which a person decides about. Diary cards are
left alone: they are signed with a digest, and an edit from outside the
compile would make them the family's for good.

One commit by the memory bot, through the curator, each file written on
the version it has in the curator's clone, so an edit made meanwhile
makes the curator refuse it and nothing is written; the next `stack up
memory` reads again. From then on the archivist writes tags this way.
Undo: revert the commit in family/memory.
"""

from __future__ import annotations

from ..lib import (
    memory_for_migration,
    tag_topics_by_id,
)


def run(ctx):
    memory, vault = memory_for_migration(ctx)
    found, commit = tag_topics_by_id(
        memory, vault, trailer=ctx.trailer)
    for line in found.left:
        ctx.step(f"Memory: {line}; left as it is")
    if found.changes:
        ctx.step(f"Memory: tagged the topics of {len(found.changes)} card(s) by id")
    return commit
