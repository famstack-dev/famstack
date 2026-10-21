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

One commit by the memory bot, written against the blob each file was
read at from a fresh clone, so an edit made meanwhile makes Forgejo
refuse it and nothing is written; the next `stack up memory` reads
again. From then on the archivist writes tags this way. Undo: revert the
commit in family/memory.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from lib import (
    BOT_USERNAME,
    authenticated_remote,
    ensure_vault_cloned,
    host_code_url,
    tag_topics_by_id,
    vault_remote_url,
)
from stack.forgejo import ForgejoClient


def run(ctx):
    code_url = host_code_url(ctx.env.get("CODE_URL", ""))
    token = ctx.secret("MEMORY_BOT_TOKEN")
    if not (code_url and token):
        raise RuntimeError("no Forgejo address or memory write token on file")
    remote = authenticated_remote(vault_remote_url(code_url), BOT_USERNAME, token)
    with tempfile.TemporaryDirectory(prefix="memory-topic-tags-") as scratch:
        clone = Path(scratch) / "memory"
        if not ensure_vault_cloned(clone, remote):
            raise RuntimeError("could not clone family/memory to read its cards")
        found, commit = tag_topics_by_id(
            ForgejoClient(url=code_url, token=token), clone, trailer=ctx.trailer)
    for line in found.left:
        ctx.step(f"Memory: {line}; left as it is")
    if found.changes:
        ctx.step(f"Memory: tagged the topics of {len(found.changes)} card(s) by id")
    return commit
