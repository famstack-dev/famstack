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

import hashlib
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


# ── Where a capture is filed ──────────────────────────────────────────
#
# A note, a bookmark or an email is filed under the person or topic it
# belongs to, named after its title and a hash of what identifies it (its
# link or its words). The archivist and the memory's cards API name a
# capture's file by these rules, so both find the same file.

def capture_hash(hash_key: str, hash_len: int = 6) -> str:
    """The stable short hash that identifies a capture in its filename.

    Used both to build a capture path and to *find* an existing one: an
    email thread is keyed by its thread-root URI, so every message in the
    conversation produces the same hash even as the title (and thus the
    slug) drifts. The mirror reuses that to append replies to the one
    thread file instead of spawning a new file per message.
    """
    return hashlib.sha256(
        hash_key.encode("utf-8") if hash_key else b"",
    ).hexdigest()[:hash_len]


def capture_filepath(
    entity: str,
    kind: str,
    captured_at: str,
    title: str | None,
    hash_key: str,
    hash_len: int = 6,
    date_prefix: bool = False,
) -> str:
    """Build ``<entity>/<kind>s/YYYY/MM/<slug>-<hash>.md``.

    ``entity`` is the sender's slug (Matrix localpart, lowercased).
    ``kind`` is "note", "bookmark", or "email"; the folder is the plural.

    ``hash_key`` is whatever stable string the caller wants to identify
    this capture by: typically the source URL for fetched/pasted
    captures with a link, or a content hash when the paste has no
    embedded source URL. The same key yields the same path on
    re-publish — idempotent update vs. duplicate.

    ``date_prefix`` prepends ``YYYY-MM-DD-`` to the slug so the month
    folder sorts chronologically rather than alphabetically — the same
    convention the documents path uses (``YYYY-MM-DD-<slug>-p<id>.md``).
    For a folded email thread, the date is the thread's first message, so
    the prefix is stable as replies append.

    Invalid ``captured_at`` falls back to
    ``<entity>/<kind>s/_unfiled/<slug>-<hash>.md`` — same convention
    the documents path uses for entries without a usable date.

    Args:
        entity: Sender's slug (lowercased Matrix localpart).
        kind: "note", "bookmark", or "email".
        captured_at: Capture date (YYYY-MM-DD) or None.
        title: Classification title or None.
        hash_key: Stable string for idempotent path generation.
        hash_len: Length of the hash prefix (default 6).
        date_prefix: Prepend the date to the slug for chronological sort.

    Returns:
        Vault-relative path string.

    Examples:
        >>> capture_filepath("homer", "bookmark", "2025-03-27", "Reddit Thread", "https://reddit.com/r/...")
        'homer/bookmarks/2025/03/reddit-thread-xxxxxx.md'
        >>> capture_filepath("marge", "note", "2025-03-27", "Meeting notes", "")
        'marge/notes/2025/03/meeting-notes-xxxxxx.md'
        >>> capture_filepath("homer", "note", None, "Random thought", "content hash")
        'homer/notes/_unfiled/random-thought-xxxxxx.md'
        >>> capture_filepath("family", "email", "2026-06-21", "Elternabend", "mid:x", date_prefix=True)
        'family/emails/2026/06/2026-06-21-elternabend-xxxxxx.md'
    """
    digest = capture_hash(hash_key, hash_len)

    slug_str = slug(title) if title else "capture"
    kind_dir = f"{kind}s"

    if captured_at and re.match(r"^\d{4}-\d{2}-\d{2}$", captured_at):
        y, m, _ = captured_at.split("-")
        name = f"{captured_at}-{slug_str}" if date_prefix else slug_str
        return f"{entity}/{kind_dir}/{y}/{m}/{name}-{digest}.md"
    return f"{entity}/{kind_dir}/_unfiled/{slug_str}-{digest}.md"


def same_capture(target_path: str, paths: list[str]) -> str | None:
    """The capture among `paths` that has `target_path`'s identity, whatever its title.

    A capture's file is `<title-slug>-<hash>.md`: the hash is stable per
    link or words, the slug is not. So the same link sent again under new
    framing, or words classified to a new title, are found by the
    `<entity>/<kind>s/` folder and the `-<hash>.md` suffix, not by the
    whole path. `paths` are the files under that folder. The exact path
    wins; otherwise a same-hash file under another slug, which the caller
    then updates where it is; otherwise None.

    >>> same_capture("homer/notes/2026/10/duff-1a2b3c.md",
    ...              ["homer/notes/2026/09/beer-1a2b3c.md", "homer/notes/2026/10/tent-9f9f9f.md"])
    'homer/notes/2026/09/beer-1a2b3c.md'
    """
    suffix = "-" + target_path.rsplit("-", 1)[-1]
    match = None
    for path in paths:
        if path.endswith(suffix):
            if path == target_path:
                return path
            match = path
    return match


def capture_folder(target_path: str) -> str | None:
    """The folder `same_capture` looks in: the path's first two parts.

    >>> capture_folder("homer/notes/2026/10/duff-1a2b3c.md")
    'homer/notes'
    """
    parts = target_path.split("/")
    return f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else None


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
