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

from urllib.parse import quote


# ── Logical paths ─────────────────────────────────────────────────────
#
# Kinds are explicit nouns — the path says whether "camping" is a topic
# or a person, so the resolver never has to guess. They come in two
# families, and which one a new kind belongs to is the whole design:
#
#   entities, addressed by NAME  — `topic`, `person`
#       A name a person could type, for something that has an identity
#       beyond any one file. A trailing leaf (`todo`) selects a
#       sub-page instead of the overview.
#
#   records, addressed by ID     — `docs`, `capture`
#       One artefact, keyed by something assigned once and never
#       re-derived. Never by path: a path encodes where a thing sat on
#       the day the link was written, and these things move.
#
# A link posted into chat is permanent, so the cost of putting a kind in
# the wrong family is paid forever. When in doubt, ask what changes when
# a family renames a topic or corrects a title.

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


def go_capture(capture_id: str) -> str:
    """`/capture/<id>` — one captured note, bookmark or memo.

    A record, so it is addressed by id and never by where it sits. Its
    vault path carries the bucket, the topic slug and the title slug,
    and each of those changes on its own under ordinary use: a capture
    re-scopes when a second person joins the room, a topic gets
    renamed, a title is rewritten by a correction. A path-keyed link
    would break for three reasons the resolver cannot repair, and break
    silently, which is worse than the service URLs it replaced.

    The id is the Matrix event id, assigned once and never rewritten.
    It starts with `$` and can carry `/` in older room versions, so it
    is percent-encoded into a single path segment.

    >>> go_capture("$abc123")
    '/capture/%24abc123'
    """
    return f"/capture/{quote(str(capture_id), safe='')}"


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
