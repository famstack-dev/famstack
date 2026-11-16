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

  - A card in the memory repo, which records link (ADR-017 §3, §10):
        person        -> <shared_bucket>/people/<id>.md   (`stack.people.person_path`)
        correspondent -> <shared_bucket>/correspondents/<slug>.md
        topic         -> <bucket>/<slug>/topic.md
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


def correspondent_path(name: str, shared_bucket: str = DEFAULT_SHARED_BUCKET) -> str:
    """The correspondent's card, which every document from them links.

    >>> correspondent_path("Moe's Tavern")
    'family/correspondents/moe-s-tavern.md'
    """
    return f"{correspondents_dir(shared_bucket)}/{slug(name)}.md"


# The folders a bucket files records into. A folder of a bucket that holds
# one of them is a topic (`family/itchy-scratchy-land/notes/...`).
CAPTURE_FOLDERS = ("notes", "bookmarks", "emails", "documents")

TOPIC_CARD = "topic.md"

# Names a topic never gets, because a bucket's own folders and pages use
# them: the capture folders the mirror writes, the correspondents, the
# family's lists, the rescue folder and the wiki's `about` page. Topics
# nest inside a bucket, so only names inside one can collide.
RESERVED_TOPIC_IDS = frozenset({
    "notes", "bookmarks", "documents", "correspondents", "lists", "_unfiled", "about",
})


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
