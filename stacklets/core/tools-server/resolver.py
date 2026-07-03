"""Logical link resolution — stable `/wiki` and `/docs` paths to live URLs.

The resolver is the indirection that keeps links posted into append-only
Matrix history from rotting. A chat message carries `go.<domain>/wiki/camping`;
the resolver maps that logical path to wherever the camping topic's page lives
*right now*, in whichever hosting mode the stack runs (pretty domain or raw
IP:port). Rename a topic, flip from port mode to domain mode, move Paperless —
the frozen chat link still lands, because it re-resolves at click time.

This module is the *pure* half: logical path segments → a wiki-relative target
path. The HTTP layer (`server.py`) turns that target into a full URL using the
mode-correct public base and issues the 302. Keeping the mapping pure and
stdlib-only is what makes it unit-testable without standing up FastAPI.
"""

from __future__ import annotations

# A trailing `todo`/`todos` segment points at the scope's task list instead of
# its overview page. Both spellings accepted — people type either.
_TODO_LEAVES = {"todo", "todos"}


def resolve_wiki_target(
    segments: list[str], *, members: set[str], shared_bucket: str,
) -> str | None:
    """Map `/wiki/<segments>` to a wiki-relative target path.

    Members are top-level buckets, so a lone segment naming a member resolves
    to that member's home; any other lone segment is a shared topic under
    `shared_bucket`. A first segment that is a member or the shared bucket is
    treated as a literal vault path (so the explicit `family/camping` form and
    a personal `homer/gravel` topic both work). A `todo`/`todos` leaf selects
    the scope's `todos` page, otherwise its `about` page.

    Returns None for shapes we don't recognise, so the HTTP layer can answer
    404 rather than guess a path that 404s downstream anyway.
    """
    if not segments:
        return None

    leaf = segments[-1].lower()
    wants_todos = leaf in _TODO_LEAVES
    scope = segments[:-1] if wants_todos else segments
    if not scope:
        return None

    if len(scope) == 1:
        seg = scope[0]
        bucket_path = seg if seg in members else f"{shared_bucket}/{seg}"
    elif scope[0] in members or scope[0] == shared_bucket:
        bucket_path = "/".join(scope)
    else:
        return None

    page = "todos" if wants_todos else "about"
    return f"{bucket_path}/{page}"


def build_redirect(
    kind: str, rest: list[str], *,
    docs_base: str, wiki_base: str, members: set[str], shared_bucket: str,
) -> str | None:
    """Full redirect URL for a `/<prefix>/<kind>/<rest>` request, or None.

    `kind` is `docs` or `wiki`; `rest` is the remaining path segments.
    `docs_base`/`wiki_base` are the public base URLs of Paperless and the
    wiki — already mode-correct, computed once by the HTTP layer from env, so
    this stays a pure string join. None (→ 404) for an unknown kind, a
    non-numeric doc id, or a wiki shape the resolver rejects.
    """
    if kind == "docs":
        if len(rest) != 1 or not rest[0].isdigit():
            return None
        return f"{docs_base.rstrip('/')}/documents/{rest[0]}/details"
    if kind == "wiki":
        target = resolve_wiki_target(
            rest, members=members, shared_bucket=shared_bucket,
        )
        if target is None:
            return None
        return f"{wiki_base.rstrip('/')}/{target}"
    return None
