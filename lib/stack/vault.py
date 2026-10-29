"""Vault layout — how ontology entities project to vault file paths.

Pure path/slug conventions shared across stacklets: the slug rules, where
each kind of entity page lives, and how to compute a relative link from
one vault file to another. No I/O, no third-party deps.

Both the docs archivist (which writes document and capture mirrors) and
the memory wiki (which writes entity and topic pages) import from here,
so neither stacklet has to reach into the other's internals for a shared
convention. This is the projection layer that sits between the ontology
(`stack.ontology` — what entities *are*) and the vault on disk (where
their pages *live*).

Where things live:

  - A view the wiki compiles (`entity_page_path`), in the brain:
        person        -> <id>/about.md          (id = the Matrix localpart)
        correspondent -> <shared_bucket>/correspondents/<slug>/about.md
        topic         -> <bucket>/<slug>/about.md
  - A card in the memory repo, which records link (ADR-017 §3, §10):
        person        -> <shared_bucket>/people/<id>.md   (`stack.people.person_path`)
        correspondent -> <shared_bucket>/correspondents/<slug>.md
        topic         -> <bucket>/<slug>/topic.md
"""

from __future__ import annotations

import posixpath

from .slug import path_slug


DEFAULT_SHARED_BUCKET = "family"


def slugify_person(name: str) -> str:
    """Map a person name to its vault bucket slug.

    Buckets are the Matrix localpart lowercased; with stock user seeding
    that is the first name lowercased ("Homer Simpson" -> "homer"). We
    take the first whitespace token so a full name still resolves to the
    bucket the captures landed in. The person's container entity page
    lives at ``<bucket>/about.md``.

    >>> slugify_person("Homer Simpson")
    'homer'
    >>> slugify_person("marge")
    'marge'
    >>> slugify_person("")
    ''
    """
    token = name.strip().split()[0] if name.strip() else ""
    return token.lower()


def correspondents_dir(shared_bucket: str = DEFAULT_SHARED_BUCKET) -> str:
    """Repo-relative path to the correspondents folder for a bucket."""
    return f"{shared_bucket}/correspondents"


def entity_page_path(name: str, kind: str, shared_bucket: str = DEFAULT_SHARED_BUCKET) -> str:
    """Vault-relative path to the page describing an entity.

    ``kind`` selects the layout (see module docstring):
      - "person": container entity at ``<bucket-slug>/about.md``.
      - "correspondent": leaf entity at
        ``<shared_bucket>/correspondents/<slug>/about.md``, beside its card.

    >>> entity_page_path("Homer Simpson", "person")
    'homer/about.md'
    >>> entity_page_path("Duff Insurance", "correspondent")
    'family/correspondents/duff-insurance/about.md'
    """
    if kind == "person":
        return f"{slugify_person(name)}/about.md"
    if kind == "correspondent":
        return f"{correspondent_path(name, shared_bucket).removesuffix('.md')}/about.md"
    raise ValueError(f"unknown entity kind: {kind!r}")


def correspondent_path(name: str, shared_bucket: str = DEFAULT_SHARED_BUCKET) -> str:
    """The correspondent's card, which every document from them links.

    >>> correspondent_path("Moe's Tavern")
    'family/correspondents/moe-s-tavern.md'
    """
    return f"{correspondents_dir(shared_bucket)}/{path_slug(name)}.md"


# The folders a bucket files records into. A folder of a bucket that holds
# one of them is a topic (`family/itchy-scratchy-land/notes/...`).
CAPTURE_FOLDERS = ("notes", "bookmarks", "emails", "documents")

TOPIC_CARD = "topic.md"


def topic_of(path: str) -> tuple[str, str] | None:
    """(bucket, topic slug) of a record filed into a topic, None for any other path.

    >>> topic_of("family/itchy-scratchy-land/notes/2026/10/tent-3a338e.md")
    ('family', 'itchy-scratchy-land')
    >>> topic_of("homer/notes/2026/10/tent-3a338e.md") is None
    True
    """
    parts = path.split("/")
    if len(parts) >= 4 and parts[2] in CAPTURE_FOLDERS and parts[1] not in CAPTURE_FOLDERS:
        return parts[0], parts[1]
    return None


def topic_card_path(bucket: str, topic: str) -> str:
    """The topic's card, in the topic's own folder.

    >>> topic_card_path("family", "itchy-scratchy-land")
    'family/itchy-scratchy-land/topic.md'
    """
    return f"{bucket}/{topic}/{TOPIC_CARD}"


def relative_link(target: str, from_path: str) -> str:
    """The link from vault file `from_path` to vault file `target`.

    Relative to the directory of `from_path`, so the link resolves in
    Obsidian, in Forgejo and as an OKF graph edge (ADR-017 §3).

    >>> relative_link("family/people/homer.md", "family/documents/2026/03/x.md")
    '../../../people/homer.md'
    >>> relative_link("family/people/homer.md", "x.md")
    'family/people/homer.md'
    """
    base = posixpath.dirname(from_path)
    return posixpath.relpath(target, base) if base else target
