"""Name the people of every diary card by id.

ADR-017 §4. In each diary card the diary compile still owns, a `persons`
entry a declared person goes by (`family/people/<id>.md`, by any name
they go by) becomes their id, every `Person: <name>` tag goes, and the
card is signed again with the digest of its new text, so it stays the
compile's. Only those lines change; every other byte stays as it was.

Left as it is and named in the output: a name no declared person goes
by, and a card a person changed (its digest no longer matches, or it has
none), which is theirs and keeps its names and tags. The migration
stops, and runs again on the next `stack up memory`, while no person
files exist.

One commit by the memory bot, written against the blob each file was
read at from a fresh clone, so an edit made meanwhile makes Forgejo
refuse it and nothing is written; the next `stack up memory` reads
again. From then on the diary compile writes cards this way. Undo:
revert the commit in family/memory.
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
    name_diary_people_by_id,
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
    with tempfile.TemporaryDirectory(prefix="memory-diary-people-") as scratch:
        clone = Path(scratch) / "memory"
        if not ensure_vault_cloned(clone, remote):
            raise RuntimeError("could not clone family/memory to read its diary cards")
        found, commit = name_diary_people_by_id(
            memory_forgejo(code_url, token=token), clone,
            shared_bucket=bucket, trailer=ctx.trailer)
    for line in found.left:
        ctx.step(f"Memory: {line}; left as it is")
    if found.changes:
        ctx.step(f"Memory: named the people of {len(found.changes)} diary card(s) by id")
    return commit
