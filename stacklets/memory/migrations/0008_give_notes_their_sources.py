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

One commit by the memory bot, written against the blob each card was read
at from a fresh clone, each sidecar as a new file, so an edit made
meanwhile makes Forgejo refuse it and nothing is written; the next `stack
up memory` reads again. A second run finds nothing to convert. Undo:
revert the commit in family/memory.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from lib import (
    BOT_USERNAME,
    authenticated_remote,
    ensure_vault_cloned,
    give_notes_their_sources,
    host_code_url,
    vault_remote_url,
)
from stack.forgejo import ForgejoClient

REPO = "family/memory"


def run(ctx):
    code_url = host_code_url(ctx.env.get("CODE_URL", ""))
    token = ctx.secret("MEMORY_BOT_TOKEN")
    if not (code_url and token):
        raise RuntimeError("no Forgejo address or memory write token on file")
    media_dir = ctx.env.get("MEDIA_ARCHIVE_DIR", "")
    remote = authenticated_remote(vault_remote_url(code_url), BOT_USERNAME, token)
    with tempfile.TemporaryDirectory(prefix="memory-note-sources-") as scratch:
        clone = Path(scratch) / "memory"
        if not ensure_vault_cloned(clone, remote):
            raise RuntimeError("could not clone family/memory to read its notes")
        found, commit = give_notes_their_sources(
            ForgejoClient(url=code_url, token=token), clone,
            media_root=Path(media_dir) if media_dir else None, trailer=ctx.trailer)
    for line in found.left:
        ctx.step(f"Memory: {line}")
    if found.created:
        ctx.step(f"Memory: gave {len(found.created)} note(s) their sources and a sidecar")
    return commit
