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

One commit by the memory bot, written against the blob each file was
read at from a fresh clone, so an edit made meanwhile makes Forgejo
refuse it and nothing is written; the next `stack up memory` reads
again. From then on the nightly move and the diary compile write the
kind. Undo: revert the commit in family/memory.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from lib import (
    memory_forgejo,
    BOT_USERNAME,
    authenticated_remote,
    ensure_vault_cloned,
    give_card_kinds,
    host_code_url,
    vault_remote_url,
)

REPO = "family/memory"


def run(ctx):
    code_url = host_code_url(ctx.env.get("CODE_URL", ""))
    token = ctx.secret("MEMORY_BOT_TOKEN")
    if not (code_url and token):
        raise RuntimeError("no Forgejo address or memory write token on file")
    remote = authenticated_remote(vault_remote_url(code_url), BOT_USERNAME, token)
    with tempfile.TemporaryDirectory(prefix="memory-kinds-") as scratch:
        clone = Path(scratch) / "memory"
        if not ensure_vault_cloned(clone, remote):
            raise RuntimeError("could not clone family/memory to read its cards")
        found, commit = give_card_kinds(
            memory_forgejo(code_url, token=token), clone, trailer=ctx.trailer)
    for line in found.left:
        ctx.step(f"Memory: {line}; left as it is")
    if found.changes:
        ctx.step(f"Memory: gave {len(found.changes)} card(s) their kind")
    return commit
