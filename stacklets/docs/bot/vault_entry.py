"""Vault entry formatting — path generation, frontmatter, markdown rendering.

A vault entry is one markdown file mirroring a Paperless document or a
capture (an OKF "document" within the vault bundle). This module owns the
three pure steps that produce it: pick the path, build the frontmatter,
render the body.

No Forgejo I/O, no git operations, no filesystem writes. These functions
take structured data and produce strings (filepaths, YAML, markdown).

Extracted from git_mirror.py so the deriver (Phase 2) can render the same
markdown shape without pulling in the GitMirror class.

The vault layout is entity-rooted. Every entity — each family member
(`homer/`, `marge/`, …) and the shared institutional bucket
(`family/` by default, slug configurable via `stack.toml [core]
shared_bucket`) — sits at the vault root with the same shape.

Documents go to the shared bucket:

    <shared_bucket>/documents/YYYY/MM/YYYY-MM-DD-<slug>-p<id>.md
    <shared_bucket>/documents/_unfiled/p<id>.md            (no date)

Filename uses a title slug when AI classification produced one,
falls back to the Paperless id otherwise. The filename is stable
after the first AI pass — a later reprocess updates content but
doesn't chase title tweaks across the URL space.

Captures (URL bookmarks, pasted notes) route to the sender's entity:

    <sender>/notes/YYYY/MM/<slug>-<hash>.md          (kind: note)
    <sender>/bookmarks/YYYY/MM/<slug>-<hash>.md      (kind: bookmark)
    <sender>/notes/_unfiled/<slug>-<hash>.md         (no date)
"""

from __future__ import annotations

import hashlib
import re

import yaml

# Frontmatter writer uses the shared stdlib module so reader/writer agree.
from stack.frontmatter import dump as frontmatter_dump

# Slug and entity-path conventions live in the framework (`stack.vault`)
# so the memory wiki and this docs archivist share one source.
from stack.vault import slug, entity_relpath  # noqa: F401  (slug re-exported for callers/tests)
from stack.email_message import defang_links


def document_filepath(
    shared_bucket: str,
    date: str | None,
    paperless_id: int,
    title: str | None,
    has_title: bool,
) -> str:
    """Build ``<shared_bucket>/documents/YYYY/MM/YYYY-MM-DD-<slug>-p<id>.md``.

    ``has_title`` is True when we have a slug-worthy title (from AI
    classification or the caller's fallback filename) — as opposed
    to the generic ``Paperless #N``. The ``-p<id>`` suffix always appears
    so the Paperless ID is recoverable from the filename alone,
    surviving cache loss without needing to scan frontmatter.

    Documents live in the shared bucket because they are institutional
    artifacts — a marriage certificate or a family insurance bill
    has no single personal owner. Per-person indexing happens via
    the frontmatter ``persons:`` field, not the path.

    Args:
        shared_bucket: The institutional bucket slug (default "family").
        date: Document date (YYYY-MM-DD) or None.
        paperless_id: Paperless document ID.
        title: Classification title or None.
        has_title: Whether the title is slug-worthy (not generic).

    Returns:
        Vault-relative path string.

    Examples:
        >>> document_filepath("family", "2025-03-27", 42, "Duff Insurance - Kfz", True)
        'family/documents/2025/03/2025-03-27-duff-insurance-kfz-p42.md'
        >>> document_filepath("family", "2025-03-27", 42, None, False)
        'family/documents/2025/03/2025-03-27-p42.md'
        >>> document_filepath("family", None, 42, "Duff Insurance - Kfz", True)
        'family/documents/_unfiled/duff-insurance-kfz-p42.md'
        >>> document_filepath("family", None, 42, None, False)
        'family/documents/_unfiled/p42.md'
    """
    documents_root = f"{shared_bucket}/documents"
    if date and re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        y, m, _ = date.split("-")
        prefix = f"{documents_root}/{y}/{m}/{date}"
    else:
        prefix = f"{documents_root}/_unfiled"

    unfiled = f"{documents_root}/_unfiled"
    if has_title and title:
        slug_str = slug(title)
        return f"{prefix}-{slug_str}-p{paperless_id}.md" if prefix != unfiled else f"{unfiled}/{slug_str}-p{paperless_id}.md"
    return f"{prefix}-p{paperless_id}.md" if prefix != unfiled else f"{unfiled}/p{paperless_id}.md"


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


def document_resource_url(paperless_url: str, paperless_id: int) -> str:
    """The canonical URI of a document: its Paperless details page.

    OKF's ``resource`` field and the human-facing "Show Document" link
    both point here. ``paperless_url`` is the instance base (not
    page-specific); this composes the per-document path off it. Empty
    when no public Paperless URL is configured.

    >>> document_resource_url("http://docs.home.local", 247)
    'http://docs.home.local/documents/247/details'
    >>> document_resource_url("", 247)
    ''
    """
    if not paperless_url:
        return ""
    return f"{paperless_url.rstrip('/')}/documents/{paperless_id}/details"


def document_frontmatter(
    *,
    title: str,
    date: str | None,
    correspondent: str | None,
    document_type: str | None,
    category: str | None,
    persons: list[str],
    tags: list[str],
    paperless_id: int,
    paperless_url: str,
    processing: str,
    model: str | None,
    paperless_version: str = "",
) -> dict:
    """Assemble the frontmatter dict for a document mirror entry.

    Keys are in a stable order for deterministic YAML output.

    Args:
        title: Document title.
        date: Document date (YYYY-MM-DD) or None.
        correspondent: Correspondent name or None.
        document_type: Document type or None.
        category: Top-level category tag or None.
        persons: List of person names.
        tags: List of topic tags.
        paperless_id: Paperless document ID.
        paperless_url: Public Paperless URL (base, not page-specific).
        processing: Processing provenance ("ai_formatted", "ocr", "original").
        model: Model name used for classification/reformat, or None.
        paperless_version: Paperless server version, if known.

    Returns:
        Frontmatter dict ready for YAML serialization.
    """
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    # `type` is the OKF-required concept kind ("document"). It is a
    # different axis from `document_type` (the Paperless subtype:
    # invoice, contract); both coexist. First key per OKF convention.
    fm: dict = {"type": "document", "title": title}
    if date:
        fm["date"] = date
    if correspondent:
        fm["correspondent"] = correspondent
    if document_type:
        fm["document_type"] = document_type
    if category:
        fm["category"] = category
    if persons:
        fm["persons"] = persons
    if tags:
        fm["tags"] = tags
    fm["paperless_id"] = paperless_id
    if paperless_url:
        fm["paperless_url"] = paperless_url
        # OKF `resource`: the URI of this specific document (its Paperless
        # details page), not the instance root kept in `paperless_url`.
        fm["resource"] = document_resource_url(paperless_url, paperless_id)
    fm["processing"] = processing
    if model:
        fm["model"] = model
    if paperless_version:
        fm["paperless_version"] = paperless_version
    fm["source"] = "paperless"
    fm["timestamp"] = now
    return fm


def capture_frontmatter(
    *,
    title: str,
    captured_at: str,
    kind: str,
    source_uri: str | None,
    persons: list[str],
    tags: list[str],
    model: str | None,
    capture_id: str | None = None,
    filed_by: str | None = None,
) -> dict:
    """Frontmatter for a capture entry.

    ``kind`` is "bookmark" (URL pointer + LLM summary) or "note"
    (pasted body the user typed). Document-shaped fields
    (correspondent, document_type, category, paperless_id,
    paperless_url) are intentionally absent — captures aren't part
    of the Paperless ontology.

    The OKF ``resource`` field (the source URL) is optional — a pure
    text note with no embedded link omits it entirely, so a Dataview
    ``where resource`` cleanly filters to "captures that point at a
    source." It is fed from the ``source_uri`` argument.

    ``date`` carries the capture date — the article's own publish
    date (if any) lives in the briefing block. The capture log is
    a record of *when we captured*, not when the source published.

    Args:
        title: Capture title.
        captured_at: Capture date (YYYY-MM-DD).
        kind: "bookmark" or "note".
        source_uri: Original source URL, or None.
        persons: List of person names.
        tags: List of topic tags.
        model: Model name used for classification, or None.
        capture_id: Stable capture identifier carried on the
            ``dev.famstack.event`` envelope, or None.

    Returns:
        Frontmatter dict ready for YAML serialization.
    """
    import datetime as dt
    now = (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    # `type` is the OKF-required concept kind (note/bookmark). It is the
    # single home for that distinction; there is no separate `kind` key.
    fm: dict = {"type": kind, "title": title}
    if captured_at:
        fm["date"] = captured_at
    if persons:
        fm["persons"] = persons
    if filed_by:
        # The Matrix localpart of whoever filed this capture. Mirrors the git
        # commit author so "who added this" survives outside a git context
        # (Dataview queries, a plain read of the file).
        fm["filed_by"] = filed_by
    if tags:
        fm["tags"] = tags
    if source_uri:
        # OKF `resource`: the URI of the underlying asset this concept
        # describes (the captured source URL).
        fm["resource"] = source_uri
    if capture_id:
        # Same identifier the `dev.famstack.event` envelope carries under
        # `data.capture_id`. Stored on the file too so a later grep (or
        # the deriver) can find this entry without depending on the
        # mutable vault path.
        fm["capture_id"] = capture_id
    if model:
        fm["model"] = model
    fm["timestamp"] = now
    return fm


def render_document(
    *,
    frontmatter: dict,
    body: str,
    correspondent: str | None,
    persons: list[str],
    from_path: str,
    shared_bucket: str,
    summary: str | None = None,
    facts: list | None = None,
    action_items: list | None = None,
    source_link: tuple[str, str] | None = None,
) -> str:
    """Assemble the mirror file for a Paperless document.

    Layout, from top to bottom:

      - YAML frontmatter (machine view: structured metadata)
      - H1 title
      - entity-link header (``**From:** [Duff Insurance](…) · **About:** [Homer](…)``)
        as relative markdown links, so they resolve in Obsidian, on
        GitHub/Forgejo, and as OKF graph edges.
      - **briefing callout** — ``> [!summary]`` with prose, optional
        source link, facts, and action items. Wrapped in a callout
        so the briefing reads as a distinct block from the OCR body
        (Obsidian renders a tinted box; Forgejo falls back to a
        labeled blockquote).
      - the OCR-cleaned document body

    Args:
        frontmatter: YAML frontmatter dict.
        body: Document body text (OCR-cleaned or reformatted).
        correspondent: Correspondent name for the entity header.
        persons: List of person names for the entity header.
        from_path: This entry's own vault path, for relative link math.
        shared_bucket: The institutional bucket slug (for correspondents).
        summary: Prose summary for the briefing callout.
        facts: List of fact strings for the briefing.
        action_items: List of action item dicts or strings.
        source_link: ``(label, url)`` tuple for the "Show Document" link.

    Returns:
        Complete markdown string for the mirror file.
    """
    fm_yaml = frontmatter_dump(frontmatter)
    parts = ["---", fm_yaml, "---", ""]

    parts.append(f"# {frontmatter.get('title', 'Untitled')}")
    parts.append("")

    if (correspondent or persons):
        bits = []
        if correspondent:
            href = entity_relpath(correspondent, "correspondent", from_path, shared_bucket)
            bits.append(f"**From:** [{correspondent}]({href})")
        if persons:
            bits.append("**About:** " + ", ".join(
                f"[{p}]({entity_relpath(p, 'person', from_path, shared_bucket)})"
                for p in persons
            ))
        parts.append("> " + " · ".join(bits))
        parts.append("")

    briefing = _briefing_block(
        summary=summary, facts=facts, action_items=action_items,
        source_link=source_link,
    )
    if briefing:
        parts.append(briefing)
        parts.append("")

    body_stripped = body.strip() if body else ""
    if body_stripped:
        parts.append(body_stripped)
        parts.append("")
    return "\n".join(parts)


def render_capture(
    *,
    frontmatter: dict,
    body: str,
    kind: str,
    captured_at: str | None,
    source_uri: str | None,
    persons: list[str],
    from_path: str,
    shared_bucket: str,
    summary: str | None = None,
    facts: list | None = None,
    action_items: list | None = None,
) -> str:
    """Assemble the mirror file for a capture entry (kind=note|bookmark).

    Captures diverge from documents in three ways:

      1. The meta block uses Captured/Kind/Source instead of the
         document's From/About/Date/Type/Category.
      2. Action items ride in the briefing callout only when the caller
         extracted them — human-typed notes opt in, bookmarks never do
         (a saved Reddit thread is not a todo).
      3. ``kind: note`` keeps the user's pasted text but tucks it inside
         an Obsidian collapsible callout. The summary is what the eye
         lands on; verifying the original is one click away.
         ``kind: bookmark`` has no body at all — the URL plus the
         summary IS the entry.

    Args:
        frontmatter: YAML frontmatter dict.
        body: Capture body text (empty for bookmarks, pasted text for notes).
        kind: "bookmark" or "note".
        captured_at: Capture date.
        source_uri: Original source URL, or None.
        persons: List of person names.
        from_path: This entry's own vault path, for relative link math.
        shared_bucket: The institutional bucket slug (for correspondents).
        summary: Prose summary for the briefing callout.
        facts: List of fact strings for the briefing.
        action_items: Tasks the note records (notes only); [] / None for
            bookmarks. Rendered as `- [ ]` lines in the briefing callout.

    Returns:
        Complete markdown string for the mirror file.
    """
    fm_yaml = frontmatter_dump(frontmatter)
    parts = ["---", fm_yaml, "---", ""]

    parts.append(f"# {frontmatter.get('title', 'Untitled')}")
    parts.append("")

    meta_lines: list[str] = []
    if persons:
        meta_lines.append(
            "**About** " + ", ".join(
                f"[{p}]({entity_relpath(p, 'person', from_path, shared_bucket)})"
                for p in persons
            )
        )
    line2_bits = []
    if captured_at:
        line2_bits.append(f"**Captured** {captured_at}")
    line2_bits.append(f"**Kind** {kind}")
    meta_lines.append(" · ".join(line2_bits))
    if source_uri:
        meta_lines.append(f"**Source** <{source_uri}>")
    parts.extend(f"> {ln}" for ln in meta_lines)
    parts.append("")

    # Briefing — summary + facts, plus action items when the note carried
    # any (bookmarks pass None, so nothing changes for them).
    briefing = _briefing_block(
        summary=summary, facts=facts, action_items=action_items,
    )
    if briefing:
        parts.append(briefing)
        parts.append("")

    # Notes: collapsible callout around the verbatim paste. The `-`
    # after [!quote] tells Obsidian to default-collapse the section.
    # Forgejo's renderer falls back to a labeled blockquote.
    if kind == "note":
        body_stripped = body.strip() if body else ""
        if body_stripped:
            parts.append("> [!quote]- Original paste")
            for ln in body_stripped.split("\n"):
                parts.append(f"> {ln}" if ln else ">")
            parts.append("")

    return "\n".join(parts)


# ── Email threads ──────────────────────────────────────────────────────
#
# Email is the one capture source that accumulates. A URL or a note is a
# single artifact; a conversation is many messages that belong together,
# so they fold into ONE file keyed by the thread root Message-ID (the
# identity ADR-010 reprocessing replays):
#
#     <bucket>/emails/YYYY/MM/<subject-slug>-<hash>.md
#
# where <hash> is over the thread root, so every reply resolves to the
# same path. The file is a stack of dated message sections under a shared
# H1 + frontmatter. Each section carries an HTML-comment `mid:` marker so
# re-folding the same message is a no-op — idempotency lives in the file
# itself, not in external state (the vault is the source of truth).
#
# Unlike bookmarks, email keeps action items: "return the form by Friday"
# is exactly the kind of task the brain should surface.


def email_mid_marker(message_id: str) -> str:
    """The per-section idempotency marker for one message in a thread."""
    return f"<!-- mid:{message_id} -->"


def render_email_message_section(
    *,
    message_id: str | None,
    from_addr: str | None,
    captured_at: str | None,
    body: str,
    summary: str | None = None,
    facts: list | None = None,
    action_items: list | None = None,
) -> str:
    """Render one message as a dated section of its thread file.

    Layout: the idempotency marker comment, an H2 heading (date — sender),
    the per-message briefing callout (summary, facts, action items), then
    the verbatim message body in a collapsible quote callout. Trailing
    newline so sections concatenate with a blank line between them.
    """
    parts: list[str] = []
    if message_id:
        parts.append(email_mid_marker(message_id))
    heading_bits = [b for b in (captured_at, from_addr) if b]
    parts.append("## " + (" · ".join(heading_bits) or "Message"))
    parts.append("")

    # The body is defanged upstream, but summary/facts are the model's own
    # prose — a URL the model lifts out of a phishing mail would otherwise
    # render as a live link. Defang here too: the URL stays readable (knowing
    # the phishing address is useful), just not clickable.
    summary = defang_links(summary) if summary else summary
    facts = [defang_links(f) if isinstance(f, str) else f for f in facts] if facts else facts

    briefing = _briefing_block(
        summary=summary, facts=facts, action_items=action_items,
    )
    if briefing:
        parts.append(briefing)
        parts.append("")

    body_stripped = body.strip() if body else ""
    if body_stripped:
        parts.append("> [!quote]- Message")
        for ln in body_stripped.split("\n"):
            parts.append(f"> {ln}" if ln else ">")
    return "\n".join(parts).rstrip() + "\n"


def render_email_thread(
    *,
    frontmatter: dict,
    title: str,
    captured_at: str | None,
    source_uri: str | None,
    persons: list[str],
    from_path: str,
    shared_bucket: str,
    sections: list[str],
) -> str:
    """Assemble a fresh thread file: frontmatter + H1 + meta + sections."""
    fm_yaml = frontmatter_dump(frontmatter)
    parts = ["---", fm_yaml, "---", "", f"# {title}", ""]

    meta_lines: list[str] = []
    if persons:
        meta_lines.append(
            "**About** " + ", ".join(
                f"[{p}]({entity_relpath(p, 'person', from_path, shared_bucket)})"
                for p in persons
            )
        )
    line2 = []
    if captured_at:
        line2.append(f"**Started** {captured_at}")
    line2.append("**Kind** email")
    meta_lines.append(" · ".join(line2))
    if source_uri:
        meta_lines.append(f"**Thread** <{source_uri}>")
    parts.extend(f"> {ln}" for ln in meta_lines)

    for section in sections:
        parts.append("")
        parts.append(section.rstrip())
    return "\n".join(parts).rstrip() + "\n"


def split_frontmatter(content: str) -> tuple[dict, str]:
    """Split a markdown file into (frontmatter dict, body after frontmatter).

    Returns ``({}, content)`` when there is no leading ``---`` block, so a
    malformed or frontmatter-less file degrades to "all body" rather than
    raising.
    """
    if not content.startswith("---\n"):
        return ({}, content)
    end = content.find("\n---\n", 4)
    if end < 0:
        return ({}, content)
    try:
        fm = yaml.safe_load(content[4:end])
    except yaml.YAMLError:
        fm = None
    body = content[end + len("\n---\n"):]
    return (fm if isinstance(fm, dict) else {}, body)


def merge_email_frontmatter(
    old: dict, *, persons: list[str], tags: list[str],
) -> dict:
    """Union a new message's persons and tags into the thread frontmatter.

    A reply can loop in a new person or surface a new topic; the thread
    file's frontmatter is the union across every folded message so person-
    and tag-indexing span the whole conversation. Existing values keep
    their order, new ones append, duplicates collapse.
    """
    fm = dict(old)
    for key, incoming in (("persons", persons), ("tags", tags)):
        if not incoming:
            continue
        merged = list(fm.get(key) or [])
        for v in incoming:
            if v and v not in merged:
                merged.append(v)
        if merged:
            fm[key] = merged
    return fm


def fold_email_message(
    existing_content: str | None,
    *,
    section: str,
    message_id: str | None,
    new_frontmatter: dict,
    title: str,
    captured_at: str | None,
    source_uri: str | None,
    persons: list[str],
    tags: list[str],
    from_path: str,
    shared_bucket: str,
) -> str | None:
    """Fold one message into its thread file; return the new file content.

    Three cases:
      - First message of a thread (no existing file) → render the shell
        plus this section.
      - Already-folded message (its ``mid:`` marker is in the file) →
        return ``None`` so the caller skips the write (idempotent).
      - A new message in an existing thread → append the section and union
        its persons/tags into the existing frontmatter.
    """
    if not existing_content:
        return render_email_thread(
            frontmatter=new_frontmatter, title=title,
            captured_at=captured_at, source_uri=source_uri,
            persons=persons, from_path=from_path,
            shared_bucket=shared_bucket, sections=[section],
        )
    if message_id and email_mid_marker(message_id) in existing_content:
        return None
    fm_old, body_old = split_frontmatter(existing_content)
    fm_merged = merge_email_frontmatter(fm_old, persons=persons, tags=tags)
    fm_yaml = frontmatter_dump(fm_merged)
    return (
        f"---\n{fm_yaml}\n---\n"
        + body_old.rstrip()
        + "\n\n"
        + section.rstrip()
        + "\n"
    )


# ── Briefing block ───────────────────────────────────────────────────
#
# The briefing is the classifier's per-document take, rendered as an
# Obsidian ``> [!summary]`` callout so it reads as a distinct block —
# not "yet another H2 section that looks identical to the body". The
# callout's tinted styling in Obsidian (and labeled blockquote in
# Forgejo) keeps the LLM-extracted view visually separate from the
# OCR-cleaned content that follows.
#
# ``source_link``, when present, surfaces a direct link inside the
# callout — for documents that's the Paperless web URL, so the user
# is one click from the original PDF without scrolling the YAML
# frontmatter or opening the file menu.
#
# Action items stay as standard task checkboxes (work inside callouts
# in Obsidian and remain Tasks-plugin-queryable).


def _briefing_block(
    *,
    summary: str | None,
    facts: list | None,
    action_items: list | None,
    source_link: tuple[str, str] | None = None,
) -> str:
    """Render the briefing as a ``> [!summary]`` callout.

    Sections are conditional: an empty prose summary, empty facts,
    or empty action items all drop out. When everything is empty the
    callout itself is suppressed — no stale ``> [!summary]`` shell.

    ``source_link`` is ``(label, url)``; when both are non-empty it
    renders as ``[label](url)`` directly under the prose.

    Args:
        summary: Prose summary text.
        facts: List of fact strings.
        action_items: List of action item dicts or strings.
        source_link: ``(label, url)`` tuple for a source link.

    Returns:
        Briefing callout string, or "" when all sections are empty.
    """
    sections: list[str] = []

    if summary and isinstance(summary, str) and summary.strip():
        sections.append(summary.strip())

    if source_link:
        label, url = source_link
        if label and url:
            sections.append(f"[{label}]({url})")

    fact_lines = _fact_lines(facts or [])
    if fact_lines:
        sections.append("**Facts**\n" + "\n".join(fact_lines))

    task_lines = _action_item_lines(action_items or [])
    if task_lines:
        sections.append("**Action items**\n" + "\n".join(task_lines))

    if not sections:
        return ""

    inner = "\n\n".join(sections)
    lines = ["> [!summary]"]
    for ln in inner.split("\n"):
        lines.append(f"> {ln}" if ln else ">")
    return "\n".join(lines)


def _fact_lines(facts: list) -> list[str]:
    out = []
    for f in facts:
        if isinstance(f, str) and f.strip():
            out.append(f"- {f.strip()}")
    return out


def _action_item_lines(items: list) -> list[str]:
    out: list[str] = []
    for ai in items:
        line = _format_action_item(ai)
        if line:
            out.append(line)
    return out


def _format_action_item(ai) -> str | None:
    """``{action, due}`` → ``- [ ] action — YYYY-MM-DD`` or ``- [ ] action``.

    Args:
        ai: Action item as a dict (with "action" and optional "due")
            or a plain string.

    Returns:
        Formatted checkbox line, or None when the item is empty/invalid.
    """
    if isinstance(ai, str):
        return f"- [ ] {ai.strip()}" if ai.strip() else None
    if not isinstance(ai, dict):
        return None
    action = (ai.get("action") or "").strip()
    if not action:
        return None
    due = ai.get("due")
    if isinstance(due, str):
        due_clean = due.strip()
        if due_clean and due_clean.lower() not in ("null", "none", "n/a"):
            return f"- [ ] {action} — {due_clean}"
    return f"- [ ] {action}"
