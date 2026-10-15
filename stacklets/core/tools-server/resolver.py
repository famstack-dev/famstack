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

from stack.cards import CARD_ID_FIELDS

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
    `homer/gravel/about`). A `todo`/`todos` leaf selects the topic's list,
    `family/lists/camping`.
    Deeper than two scope segments is not a valid topic path — None, so the
    HTTP layer 404s rather than pointing at a page that 404s anyway.
    """
    scope, page = _split_leaf(segments)
    if not scope or len(scope) > 2:
        return None
    bucket = shared_bucket if len(scope) == 1 else scope[0]
    if page == "todos":
        # A topic's todos moved to the list named after it; links posted to
        # chat before the move still open it.
        return f"{bucket}/lists/{scope[-1]}"
    return f"{bucket}/{scope[-1]}/{page}"


def resolve_list_target(segments: list[str], *, shared_bucket: str) -> str | None:
    """Map `/list/<slug>` to the family's list of that name (`family/lists/<slug>`)."""
    if len(segments) != 1 or not segments[0]:
        return None
    return f"{shared_bucket}/lists/{segments[0]}"


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
    find_card: "Callable[[str, str], str | None] | None" = None,
) -> str | None:
    """Full redirect URL for a `/<prefix>/<kind>/<rest>` request, or None.

    `kind` is `docs`, `topic`, `person`, `list`, or a card kind from
    `stack.cards` (`capture`, `diary`); `rest` is the remaining path
    segments. `docs_base`/`wiki_base` are the public base URLs of Paperless
    and the wiki — already mode-correct, computed once by the HTTP layer from
    env, so this stays a pure string join. None (→ 404) for an unknown kind, a
    non-numeric doc id, or an entity shape the resolver rejects.

    A card kind is the one whose target cannot be computed from the path:
    the card id says *which* card, never where it is now. `find_card(kind,
    card_id)` is injected by the HTTP layer for exactly that lookup and
    answers with the card's vault path, which keeps the I/O out of here and
    this module unit-testable without a vault on disk. Left unset, a card
    link 404s rather than guessing.
    """
    if kind == "docs":
        if len(rest) != 1 or not rest[0].isdigit():
            return None
        return f"{docs_base.rstrip('/')}/documents/{rest[0]}/details"
    if kind in CARD_ID_FIELDS:
        if len(rest) != 1 or find_card is None:
            return None
        path = find_card(kind, rest[0])
        # The wiki serves a page at its vault path without the suffix.
        target = path.removesuffix(".md") if path else None
    elif kind == "topic":
        target = resolve_topic_target(rest, shared_bucket=shared_bucket)
    elif kind == "person":
        target = resolve_person_target(rest)
    elif kind == "list":
        target = resolve_list_target(rest, shared_bucket=shared_bucket)
    else:
        return None
    if target is None:
        return None
    return f"{wiki_base.rstrip('/')}/{target}"
