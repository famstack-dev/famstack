"""Give every note its sources and a sidecar.

ADR-018, step 1. A note filed before kept where it came from in its
frontmatter (`capture_id`, `timestamp`, `model`, `filed_by`, `resource`)
and its words, an upload's caption and the replies in sections of their
own ("Original paste", "Caption", "Replies"). Each such note becomes a
card that keeps what it is about, with `## Sources`, one blockquote per
source, and a sidecar `<stem>.json` beside it with where each came from
and how the card was made. A photo is embedded inside its quote, followed
by its caption, and its hash comes from the media store's record. No
model is called. `made` keeps the model and the time the note was written.

What the old card did not record is kept and marked `reconstructed`, and
named in the output: a reply's time and event id, a photo the store has
no record of, words that carry prompt text from an older reprocess. A
note written in the memory, a note already converted, and every
bookmark, document, email and diary card are left as they are.

One commit by the memory bot, through the curator, each card written on
the version it has in the curator's clone, each sidecar as a new file,
so an edit made meanwhile makes the curator refuse it and nothing is
written; the next `stack up memory` reads again. A second run finds
nothing to convert. Undo: revert the commit in family/memory.
"""

from __future__ import annotations

from pathlib import Path

from ..lib import (
    give_notes_their_sources,
    memory_for_migration,
)

REPO = "family/memory"


def run(ctx):
    memory, vault = memory_for_migration(ctx)
    media_dir = ctx.env.get("MEDIA_ARCHIVE_DIR", "")
    found, commit = give_notes_their_sources(
        memory, vault,
        media_root=Path(media_dir) if media_dir else None, trailer=ctx.trailer)
    for line in found.left:
        ctx.step(f"Memory: {line}")
    if found.created:
        ctx.step(f"Memory: gave {len(found.created)} note(s) their sources and a sidecar")
    return commit
