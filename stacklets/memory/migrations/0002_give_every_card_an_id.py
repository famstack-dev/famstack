"""Give every card in the family memory an id of its own, `card_id`.

A card without one gets `<type>-<value>` (ADR-016): a document, note,
bookmark, email or diary entry ten random hex digits, a person or a list
its file's slug, a list's archive the list's slug and month. A file with
no frontmatter or no `type` was written by hand and becomes a note: it
gets `type: note`, a `title` from its first heading or else its file
name, and an id. `README.md`, `index.md` and pages the wiki compiled
(`generated: true`) are no cards and are left alone. Of two files with
one id, the one first committed later gets a new id. Only those lines
are added or rewritten; every other byte stays as it was. A file the
rule cannot place (frontmatter that does not parse, a `type` that is no
card type, a `kind` with no `type`) is left as it is and named in the
output.

One commit by the memory bot, written against the blob each file was
read at from a fresh clone, so an edit made meanwhile makes Forgejo
refuse it and nothing is written; the next `stack up memory` reads
again. From then on the writers give new cards their id and the curator
gives one to files written by hand. Undo: revert the commit in
family/memory.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from lib import (
    BOT_USERNAME,
    authenticated_remote,
    ensure_vault_cloned,
    give_card_ids,
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
    remote = authenticated_remote(vault_remote_url(code_url), BOT_USERNAME, token)
    with tempfile.TemporaryDirectory(prefix="memory-card-ids-") as scratch:
        clone = Path(scratch) / "memory"
        if not ensure_vault_cloned(clone, remote):
            raise RuntimeError("could not clone family/memory to read its cards")
        found, commit = give_card_ids(
            ForgejoClient(url=code_url, token=token), clone, trailer=ctx.trailer)
    for line in found.left:
        ctx.step(f"Memory: no card id for {line}; left as it is")
    if found.changes:
        ctx.step(f"Memory: gave {len(found.changes)} card(s) an id of their own")
    return commit
