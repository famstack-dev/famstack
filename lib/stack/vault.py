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

Entity layouts:

  - Container entities own a folder of routed content plus an `about.md`:
        person   -> <bucket-slug>/about.md      (bucket = Matrix localpart)
        topic    -> <bucket>/<slug>/about.md
  - Leaf entities are a single reference page, no folder:
        correspondent -> <shared_bucket>/correspondents/<slug>.md
"""

from __future__ import annotations

import posixpath
import re
import unicodedata


DEFAULT_SHARED_BUCKET = "family"


def slug(text: str) -> str:
    """Filesystem-safe slug: ASCII-ish, lowercase, hyphen-separated.

    The cap is a defensive ceiling, not a primary length control — the
    classifier title prompt asks for short identifying titles (no dates,
    no amounts), so well-shaped inputs land far under this cap. The slice
    is hard at 60 chars; the title prompt keeps titles human-scannable,
    not the slug.

    >>> slug("Duff Insurance - Kfz-Versicherung 2025")
    'duff-insurance-kfz-versicherung-2025'
    >>> slug("Rechnung Müller & Söhne")
    'rechnung-muller-sohne'
    >>> slug("  Leading Spaces  ")
    'leading-spaces'
    >>> slug("")
    'document'
    >>> slug("!!!")
    'document'
    """
    normalized = unicodedata.normalize("NFKD", text)
    ascii_ = normalized.encode("ascii", "ignore").decode()
    slug_str = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_).strip("-").lower()
    return slug_str[:60] or "document"


def slugify_person(name: str) -> str:
    """Map a person name to its vault bucket slug.

    Buckets are the Matrix localpart lowercased; for the default family
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
        ``<shared_bucket>/correspondents/<slug>.md``.

    >>> entity_page_path("Homer Simpson", "person")
    'homer/about.md'
    >>> entity_page_path("Duff Insurance", "correspondent")
    'family/correspondents/duff-insurance.md'
    """
    if kind == "person":
        return f"{slugify_person(name)}/about.md"
    if kind == "correspondent":
        return f"{correspondents_dir(shared_bucket)}/{slug(name)}.md"
    raise ValueError(f"unknown entity kind: {kind!r}")


def entity_relpath(
    name: str,
    kind: str,
    from_path: str,
    shared_bucket: str = DEFAULT_SHARED_BUCKET,
) -> str:
    """Relative link from ``from_path`` to an entity's page.

    The result is relative to the *directory* of ``from_path``, so the
    link resolves in Obsidian, on GitHub/Forgejo, and as an OKF graph
    edge. The target page need not exist yet (OKF treats unwritten links
    as valid "not-yet-written knowledge").

    >>> entity_relpath("Homer", "person", "family/documents/2026/03/x.md")
    '../../../../homer/about.md'
    >>> entity_relpath("Duff Insurance", "correspondent", "family/documents/2026/03/x.md")
    '../../../correspondents/duff-insurance.md'
    >>> entity_relpath("Homer", "person", "x.md")
    'homer/about.md'
    """
    target = entity_page_path(name, kind, shared_bucket)
    base = posixpath.dirname(from_path)
    return posixpath.relpath(target, base) if base else target
