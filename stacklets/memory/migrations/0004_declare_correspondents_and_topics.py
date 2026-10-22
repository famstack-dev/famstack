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

One commit by the memory bot, read from a fresh clone: a new card is
written as a new file and a changed one against the blob it was read
at, so a card that appeared or an edit made meanwhile makes Forgejo
refuse it and nothing is written; the next `stack up memory` reads
again. From then on the archivist writes these cards the first time a
record names them. Undo: revert the commit in family/memory.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from lib import (
    memory_forgejo,
    BOT_USERNAME,
    authenticated_remote,
    declare_cards,
    ensure_vault_cloned,
    host_code_url,
    vault_remote_url,
)
from stack.vault import DEFAULT_SHARED_BUCKET

REPO = "family/memory"


def run(ctx):
    code_url = host_code_url(ctx.env.get("CODE_URL", ""))
    token = ctx.secret("MEMORY_BOT_TOKEN")
    if not (code_url and token):
        raise RuntimeError("no Forgejo address or memory write token on file")
    bucket = ctx.env.get("SHARED_BUCKET") or DEFAULT_SHARED_BUCKET
    remote = authenticated_remote(vault_remote_url(code_url), BOT_USERNAME, token)
    with tempfile.TemporaryDirectory(prefix="memory-declare-cards-") as scratch:
        clone = Path(scratch) / "memory"
        if not ensure_vault_cloned(clone, remote):
            raise RuntimeError("could not clone family/memory to read its records")
        found, commit = declare_cards(
            memory_forgejo(code_url, token=token), clone,
            shared_bucket=bucket, trailer=ctx.trailer)
    if found.changes:
        ctx.step(f"Memory: declared {len(found.created)} correspondent and topic card(s), "
                 f"typed {len(found.changes) - len(found.created)} old correspondent file(s)")
    return commit
