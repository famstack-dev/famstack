"""Give every diary card its sources and a sidecar.

ADR-018. A diary card written before kept where it came from in its
frontmatter (`entry_id`, `event_ids`, `timestamp`, `filed_by`, `media`,
`model`, `resource`, `mode`, `date_basis`, `duration_ms`) and the
family's words, its replies and its files in its body. Each such card
becomes a card that keeps what the entry is about, then `## Sources`,
one blockquote per message and per reply in the order sent, and a
sidecar `<stem>.json` beside it with where each came from and how the
card was made. A photo's hash comes from the media store's record. No
model is called and no room is read.

What the old card did not record is kept and marked `reconstructed`,
and named in the output: a reply's time, a photo the store has no
record of. A card the compile owns is signed again, so the next compile
leaves it as it is. A card a person changed keeps their words and fields
and stays theirs. A card already converted, and every other card, are
left as they are.

One commit by the memory bot, through the curator, each card written on
the version it read in the curator's clone, each sidecar as a new file,
so an edit made meanwhile, pulled into the clone or not, makes the
curator refuse it and nothing is written; the next `stack up memory` reads again. A second run finds
nothing to convert. Undo: revert the commit in family/memory.

The conversion is in `old_diary_cards`, which only this migration
loads. It reaches the curator through the memory's lib
(`memory_for_migration`), as every migration before it does, so it
imports the memory's domain from the host.

Debt (arch-check R1): runs on the host and imports the memory's lib and
`old_diary_cards`. Trigger: the migrations run in the curator's
container. Then the two imports are local ones.
"""

from __future__ import annotations

from pathlib import Path

from ..lib import memory_for_migration
from ..old_diary_cards import give_diary_cards_their_sources

REPO = "family/memory"


def run(ctx):
    memory, vault = memory_for_migration(ctx)
    media_dir = ctx.env.get("MEDIA_ARCHIVE_DIR", "")
    found, commit = give_diary_cards_their_sources(
        memory, vault,
        media_root=Path(media_dir) if media_dir else None, trailer=ctx.trailer)
    for line in found.left:
        ctx.step(f"Memory: {line}")
    if found.created:
        ctx.step(f"Memory: gave {len(found.created)} diary card(s) their sources and a sidecar")
    return commit
