"""Tag the topics of every diary card by the ontology's id.

ADR-017 §4. A diary card tagged a topic by its label in the household's
language (`Gesundheit`), where every other card holds the topic's id
(`medical`). In each diary card's `tags`, a tag that names a topic of the
memory's ontology (`ontology.toml`), by its id or by its label in any of
the ontology's languages, becomes the id; every other tag is lowercased
with hyphens (`Family Life` becomes `family-life` where the ontology has
no such topic), by the rule migration 0005 applied to every other card.
Only the `tags` field changes.

A card the compile still owns is signed again with the digest of its
new text, so it stays the compile's and the next compile, a rebuild
included, finds nothing to rewrite. A card a person changed gets ids
too and stays theirs: it is not signed. Left as it is and named in the
output: a tag that is no keyword even then (`Person: Bart` on a card a
person corrected), which a person decides about.

One commit by the memory bot, written against the blob each file was
read at from a fresh clone, so an edit made meanwhile makes Forgejo
refuse it and nothing is written; the next `stack up memory` reads
again. From then on the diary compile tags this way. Undo: revert the
commit in family/memory.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from lib import (
    memory_forgejo,
    BOT_USERNAME,
    authenticated_remote,
    ensure_vault_cloned,
    host_code_url,
    tag_diary_topics_by_id,
    vault_remote_url,
)

REPO = "family/memory"


def run(ctx):
    code_url = host_code_url(ctx.env.get("CODE_URL", ""))
    token = ctx.secret("MEMORY_BOT_TOKEN")
    if not (code_url and token):
        raise RuntimeError("no Forgejo address or memory write token on file")
    remote = authenticated_remote(vault_remote_url(code_url), BOT_USERNAME, token)
    with tempfile.TemporaryDirectory(prefix="memory-diary-tags-") as scratch:
        clone = Path(scratch) / "memory"
        if not ensure_vault_cloned(clone, remote):
            raise RuntimeError("could not clone family/memory to read its diary cards")
        found, commit = tag_diary_topics_by_id(
            memory_forgejo(code_url, token=token), clone, trailer=ctx.trailer)
    for line in found.left:
        ctx.step(f"Memory: {line}; left as it is")
    if found.changes:
        ctx.step(f"Memory: tagged the topics of {len(found.changes)} diary card(s) by id")
    return commit
