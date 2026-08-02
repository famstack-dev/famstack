"""Logical link construction — the emitter half of the `/go` namespace.

A link a bot posts into a Matrix room is there forever: the timeline is
append-only, so whatever URL was in the message is the URL a family
member clicks two years later. Point it straight at a service and it
dies the day the domain changes, the stack flips between port mode and
domain mode, or Paperless moves. Point it at `home.<domain>/go/docs/247`
and it re-resolves at click time.

That indirection has two halves, and they live apart on purpose:

  * this module builds the *logical path* (`/docs/247`) and joins it onto
    the configured `/go` base. It knows the shape of the namespace and
    nothing else.
  * `stacklets/core/tools-server/resolver.py` is the only place that
    knows what a logical path resolves *to*. Nothing here may duplicate
    that mapping.

So an emitter never concatenates a service URL. It asks for a logical
path, hands it the base it was given, and posts whatever comes back.
When the base is unset (core has not rendered `LINK_BASE_URL` yet) the
answer is the empty string, and callers fall back to their unlinked
view — a link that cannot resolve is worse than no link.

Stdlib only: the host CLI imports `lib/stack/` without third-party deps,
and the bot-runner mounts this same tree at `/app/stack`, so host and
containers build links from one implementation.
"""

from __future__ import annotations


# ── Logical paths ─────────────────────────────────────────────────────
#
# Entity kinds are explicit nouns — the path says whether "camping" is a
# topic or a person, so the resolver never has to guess. A trailing leaf
# (`todo`) selects a sub-page of the entity instead of its overview.

def go_docs(doc_id: int | str) -> str:
    """`/docs/<id>` — a document, wherever it is filed right now.

    >>> go_docs(247)
    '/docs/247'
    """
    return f"/docs/{doc_id}"


def go_topic(scope: str, leaf: str | None = None) -> str:
    """`/topic/<scope>` — a shared or personal topic page.

    `scope` is either a bare slug (`camping`, resolved under the shared
    bucket) or an explicit vault path (`family/camping`, `homer/gravel`).
    Both forms are passed through verbatim; the resolver takes either.

    >>> go_topic("family/camping", "todo")
    '/topic/family/camping/todo'
    >>> go_topic("camping")
    '/topic/camping'
    """
    return _entity_path("topic", scope, leaf)


def go_person(slug: str, leaf: str | None = None) -> str:
    """`/person/<slug>` — a household member's page.

    >>> go_person("homer")
    '/person/homer'
    >>> go_person("homer", "todo")
    '/person/homer/todo'
    """
    return _entity_path("person", slug, leaf)


def _entity_path(kind: str, scope: str, leaf: str | None) -> str:
    """Join `kind`, the scope segments, and an optional leaf into a path.

    Scopes arrive from vault paths and room bindings, which carry stray
    slashes often enough that stripping them here is cheaper than at
    every call site.
    """
    segments = [s for s in str(scope).strip("/").split("/") if s]
    if leaf:
        segments.append(leaf.strip("/"))
    return "/".join(["", kind, *segments])


# ── Public URL ────────────────────────────────────────────────────────

def public(logical: str, base: str) -> str:
    """Absolute, clickable form of a logical path.

    `base` is `LINK_BASE_URL` — core's mode-correct home URL with the
    `/go` prefix already on it. Empty base means the namespace is not
    reachable yet, so there is no honest link to post.

    >>> public(go_docs(247), "https://home.example.org/go")
    'https://home.example.org/go/docs/247'
    >>> public(go_docs(247), "")
    ''
    """
    if not base:
        return ""
    return f"{base.rstrip('/')}{logical}"
