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

One commit by the memory bot, written against the blob each file was
read at from a fresh clone, so an edit made meanwhile makes Forgejo
refuse it and nothing is written; the next `stack up memory` reads
again. A second run finds no `canonical` left. Undo: revert the commit
in family/memory.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from lib import (
    BOT_USERNAME,
    authenticated_remote,
    ensure_vault_cloned,
    host_code_url,
    title_correspondents,
    vault_remote_url,
)
from stack.forgejo import ForgejoClient


def run(ctx):
    code_url = host_code_url(ctx.env.get("CODE_URL", ""))
    token = ctx.secret("MEMORY_BOT_TOKEN")
    if not (code_url and token):
        raise RuntimeError("no Forgejo address or memory write token on file")
    remote = authenticated_remote(vault_remote_url(code_url), BOT_USERNAME, token)
    with tempfile.TemporaryDirectory(prefix="memory-titles-") as scratch:
        clone = Path(scratch) / "memory"
        if not ensure_vault_cloned(clone, remote):
            raise RuntimeError("could not clone family/memory to read its cards")
        found, commit = title_correspondents(
            ForgejoClient(url=code_url, token=token), clone, trailer=ctx.trailer)
    for line in found.left:
        ctx.step(f"Memory: {line}")
    if found.changes:
        ctx.step(f"Memory: gave {len(found.changes)} correspondent(s) written by hand their title")
    return commit
