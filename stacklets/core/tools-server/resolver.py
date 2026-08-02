"""Logical link resolution — stable entity paths to live URLs.

The resolver is the indirection that keeps links posted into append-only
Matrix history from rotting. A chat message carries `home.<domain>/go/topic/camping`;
the resolver maps that logical path to wherever the camping topic's page lives
*right now*, in whichever hosting mode the stack runs (pretty domain or raw
IP:port). Rename a topic, flip from port mode to domain mode, move Paperless —
the frozen chat link still lands, because it re-resolves at click time.

Entities are first-class and explicit — `docs`, `topic`, `person` — so the noun
in the URL says which kind it is. That mirrors the CLI (`stack memory topic
camping todo`) and, crucially, means the resolver never has to *guess* whether
"camping" is a topic or a person by inspecting the household roster: the path
already told it.

This module is the *pure* half: a logical path → a wiki-relative target path.
The HTTP layer (`server.py`) turns that target into a full URL using the
mode-correct public base and issues the 302. Keeping the mapping pure and
stdlib-only is what makes it unit-testable without standing up FastAPI.
"""

from __future__ import annotations

from typing import Callable

# A trailing `todo`/`todos` segment points at the entity's task list instead of
# its overview page. Both spellings accepted — people type either.
_TODO_LEAVES = {"todo", "todos"}


def _split_leaf(segments: list[str]) -> tuple[list[str], str]:
    """Peel a `todo`/`todos` leaf off the path: returns (scope, page)."""
    if segments and segments[-1].lower() in _TODO_LEAVES:
        return segments[:-1], "todos"
    return segments, "about"


def resolve_topic_target(segments: list[str], *, shared_bucket: str) -> str | None:
    """Map `/topic/<segments>` to a wiki-relative target path.

    A lone segment is a shared topic under `shared_bucket`
    (`/topic/camping` → `family/camping/about`); a `<member>/<slug>` pair is a
    personal topic, taken as a literal vault path (`/topic/homer/gravel` →
    `homer/gravel/about`). A `todo`/`todos` leaf selects the `todos` page.
    Deeper than two scope segments is not a valid topic path — None, so the
    HTTP layer 404s rather than pointing at a page that 404s anyway.
    """
    scope, page = _split_leaf(segments)
    if not scope or len(scope) > 2:
        return None
    bucket_path = f"{shared_bucket}/{scope[0]}" if len(scope) == 1 else "/".join(scope)
    return f"{bucket_path}/{page}"


def resolve_person_target(segments: list[str]) -> str | None:
    """Map `/person/<member>` to a wiki-relative target path.

    A person is a top-level bucket, so exactly one scope segment is valid
    (`/person/homer` → `homer/about`, `/person/homer/todo` → `homer/todos`).
    """
    scope, page = _split_leaf(segments)
    if len(scope) != 1:
        return None
    return f"{scope[0]}/{page}"


def build_redirect(
    kind: str, rest: list[str], *,
    docs_base: str, wiki_base: str, shared_bucket: str,
    find_capture: "Callable[[str], str | None] | None" = None,
) -> str | None:
    """Full redirect URL for a `/<prefix>/<kind>/<rest>` request, or None.

    `kind` is `docs`, `topic`, `person`, or `capture`; `rest` is the remaining
    path segments. `docs_base`/`wiki_base` are the public base URLs of Paperless
    and the wiki — already mode-correct, computed once by the HTTP layer from
    env, so this stays a pure string join. None (→ 404) for an unknown kind, a
    non-numeric doc id, or an entity shape the resolver rejects.

    `capture` is the one kind whose target cannot be computed from the path:
    the id says *which* capture, never where it is now. `find_capture` is
    injected by the HTTP layer for exactly that lookup, which keeps the I/O out
    of here and this module unit-testable without a vault on disk. Left unset,
    `/capture/...` 404s rather than guessing.
    """
    if kind == "docs":
        if len(rest) != 1 or not rest[0].isdigit():
            return None
        return f"{docs_base.rstrip('/')}/documents/{rest[0]}/details"
    if kind == "capture":
        if len(rest) != 1 or find_capture is None:
            return None
        target = find_capture(rest[0])
    elif kind == "topic":
        target = resolve_topic_target(rest, shared_bucket=shared_bucket)
    elif kind == "person":
        target = resolve_person_target(rest)
    else:
        return None
    if target is None:
        return None
    return f"{wiki_base.rstrip('/')}/{target}"
