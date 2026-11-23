"""The card a capture is written as: its path, its frontmatter, its text and, for a note, its sidecar.

    setting = CardContext(people, vocabulary_of, shared_bucket="family", schemas=SCHEMA_DIR)
    path = capture_filepath("marge", "note", "2026-11-22", "Grill charcoal", words)
    entry = capture_entry(setting, target_path=path, existing=None, kind="note", ...)
    entry.content    # '---\\ntype: note\\ncard_id: note-7f3a9c21e4\\n...## Sources\\n...'
    entry.sidecar    # '{"card_id": "note-7f3a9c21e4", "sources": [...], "made": {...}}'

A capture becomes one of two cards. A note is its card and its sidecar
(ADR-018): the card says what it is about and quotes its sources under
`## Sources`; the sidecar keeps the sources and how the card was made. A
bookmark is one file: the link, the summary, the caption and the replies.
An email's card is folded message by message (the docs bot's
`vault_entry.fold_email_message`) and is not made here.

Pure: no I/O. Everything a card is written against comes in as a
`CardContext`: the people names resolve through, the topics of the card's
place (`stack.filing.vocabulary`), the shared bucket and the card schemas
a person's field edits are checked against. The filing core
(`stack.filing.file`) and the archivist's mirror call it; `stack memory
reprocess` shows a refile's entry without writing it.

The layout of the paths:

    <bucket>/notes/YYYY/MM/<slug>-<hash>.md          (kind: note)
    <bucket>/bookmarks/YYYY/MM/<slug>-<hash>.md      (kind: bookmark)
    <bucket>/notes/_unfiled/<slug>-<hash>.md         (no date)

The hash is over the link, else the words, so the same capture lands on
the same path (`capture_hash`).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from ..briefing import render_briefing
from ..cards import card_identity
from ..cards.fields import FieldOp, FieldValues, apply_ops
from ..cards.links import About
from ..cards.sources import Source, card_fields, render_sources, sidecar_text
from ..frontmatter import FrontmatterError
from ..frontmatter import dump as frontmatter_dump
from ..frontmatter import parse as frontmatter_parse
from ..people import Person
from ..vault import slug
from .vocabulary import Vocabulary

__all__ = ["CAPTURE_HASH_LEN", "CaptureEntry", "CardContext", "capture_entry", "card_frontmatter", "capture_filepath",
           "capture_frontmatter", "capture_hash", "classified_persons", "code_commit", "filer_of",
           "identity_for", "render_capture", "render_note"]

# How many hex digits of `capture_hash` a capture's filename carries.
CAPTURE_HASH_LEN = 6


@dataclass(frozen=True)
class CardContext:
    """What a capture's card is written against: the declared `people` its
    names resolve through, `vocabulary(path)` for the topics of a card's
    place, the `shared_bucket` (where people's cards live) and the card
    `schemas` a person's field edits are checked against.

        CardContext(vault.people(), vault.vocabulary, shared_bucket="family",
                    schemas=Path("stacklets/memory/schemas"))
    """

    people: list[Person]
    vocabulary: Callable[[str], Vocabulary]
    shared_bucket: str
    schemas: Path


class CaptureEntry(NamedTuple):
    """A capture's file as it would be written, and the people it names.

        entry = capture_entry(setting, target_path=path, existing=text, ...)
        entry.content                 # the whole file, frontmatter first
        entry.frontmatter["card_id"]  # 'note-7f3a9c21e4'
    """

    frontmatter: dict
    content: str
    about: About
    # A note's sidecar (`stack.cards.sources`), "" for every other capture.
    sidecar: str = ""
    # The field edits that no longer fit the card, each with why (`stack.cards.fields`).
    left_out: tuple[str, ...] = ()


def capture_entry(
    setting: CardContext, *,
    target_path: str,
    existing: str | None,
    kind: str,
    source_uri: str | None,
    title_hint: str | None,
    classification: dict,
    captured_at: str,
    model: str | None,
    tags: list[str] | None = None,
    capture_id: str | None = None,
    submitter: str | None = None,
    kept_media: dict | None = None,
    caption: str = "",
    replies: "list[tuple[str, str]] | tuple" = (),
    clean: str | None = None,
    sources: "Sequence[Source]" = (),
    made: dict | None = None,
    ops: "Sequence[FieldOp]" = (),
) -> CaptureEntry:
    """The files a capture is written as at `target_path`, without writing them.

    ``existing`` is the file there now, whose card id the entry keeps
    (`identity_for`). ``classification`` is the reading the card shows
    (title, persons, summary, facts, action items); ``tags`` are written
    as the topics of the card's place. A note's entry carries its sidecar
    too, with ``sources`` and ``made``, and a person's field edits ``ops``
    applied again to its card, in order (ADR-018 §7); one that no longer
    fits is left out and named in `CaptureEntry.left_out`. ``submitter``
    is who filed it, as the curator names them; the card keeps its
    localpart as `filed_by`.

        entry = capture_entry(setting, target_path="marge/notes/2026/11/grill-0c401e.md",
                              existing=None, kind="note", source_uri=None, title_hint=None,
                              classification={"title": "Grill charcoal", "persons": ["Marge"]},
                              captured_at="2026-11-22", model="qwen3", sources=[original])
        entry.frontmatter["persons"]    # ['marge']
    """
    title = classification.get("title") or title_hint or "Capture"
    persons = classified_persons(classification)
    people = setting.people
    about = About.of(persons, people)
    # SAFETY: the topics of the place the card is written to, the same
    # for a first filing, a correction and `stack memory reprocess`:
    # never the sender's or the corrector's, so a person's private topic
    # reaches no card that other people read.
    vocabulary = setting.vocabulary(target_path)
    fm = card_frontmatter(
        title=title, captured_at=captured_at, kind=kind, source_uri=source_uri,
        persons=about.ids, tags=vocabulary.topics(tags or []), model=model,
        capture_id=capture_id, filed_by=filer_of(submitter),
        identity=identity_for(existing, kind, target_path),
    )
    if kind == "note":
        card = render_note(
            frontmatter=fm, sources=sources, persons=persons, people=people,
            from_path=target_path, shared_bucket=setting.shared_bucket, clean=clean,
            summary=classification.get("summary"),
            facts=classification.get("facts") or [],
            action_items=classification.get("action_items") or [],
        )
        left_out: list[str] = []
        if ops:
            # What a field edit may name here: the topics of the card's place.
            card, left_out = apply_ops(card, ops, path=target_path, bucket=setting.shared_bucket, values=FieldValues(
                schemas=setting.schemas, person_ids=frozenset(p.id for p in people),
                ontology=vocabulary.ontology))
            fm = frontmatter_parse(card)
        return CaptureEntry(fm, card, about, sidecar_text(
            card_id=str(fm.get("card_id") or ""), filed_by=filer_of(submitter) or "",
            sources=sources, made=made or {}), tuple(left_out))
    content = render_capture(
        frontmatter=fm, kind=kind, captured_at=captured_at, source_uri=source_uri,
        persons=persons, people=people, from_path=target_path,
        shared_bucket=setting.shared_bucket,
        summary=classification.get("summary"),
        facts=classification.get("facts") or [],
        # Action items only when the classifier extracted them: notes
        # that opted in (kind == "note"); bookmarks never carry the field.
        action_items=classification.get("action_items") or [],
        kept_media=kept_media, caption=caption, replies=replies,
    )
    return CaptureEntry(fm, content, about)


def card_frontmatter(*, kind: str, **fields) -> dict:
    """A capture card's frontmatter (`capture_frontmatter`); a note's keeps
    only what the card is about, because where it came from and how it was
    made go into its sidecar (ADR-018 §3, `stack.cards.sources.card_fields`).

        card_frontmatter(kind="note", title="Grill", captured_at="2026-11-22",
                         source_uri=None, persons=["marge"], tags=[], model="qwen3")
        # {'type': 'note', 'title': 'Grill', 'date': '2026-11-22', 'persons': ['marge']}
    """
    fm = capture_frontmatter(kind=kind, **fields)
    return card_fields(fm) if kind == "note" else fm


def classified_persons(classification: dict) -> list[str]:
    """The names of the people a classification says the record is about.

        >>> classified_persons({"persons": ["Marge", "", 3]})
        ['Marge']
    """
    raw = classification.get("persons") or classification.get("person") or []
    if isinstance(raw, str):
        raw = [raw]
    return [p for p in raw if isinstance(p, str) and p]


def filer_of(submitter: str | None) -> str | None:
    """Who filed a capture as the card's `filed_by` names them: the localpart
    of the account the curator was given, lowercased; None for nobody.

        >>> filer_of("@Marge:simpson")
        'marge'
    """
    if not submitter:
        return None
    return submitter.split(":")[0].lstrip("@").strip().lower() or None


def code_commit() -> str:
    """The famstack commit this process runs, as `stack up` stamped it; a
    note's sidecar records it under `made`.

    `STACK_COMMIT` is rendered into every stacklet's env, the bot runner's
    included; a process started some other way says `unknown`.
    """
    return os.environ.get("STACK_COMMIT", "").strip() or "unknown"


def capture_hash(hash_key: str, hash_len: int = CAPTURE_HASH_LEN) -> str:
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
    hash_len: int = CAPTURE_HASH_LEN,
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


def identity_for(existing: str | None, card_type: str, path: str) -> dict:
    """The id fields a filing written to `path` carries (`stack.cards.card_identity`).

    A rebuild of a card from its source (a reprocess, a correction, an
    email thread that gains a message) replaces a file that is there
    already, and keeps that file's `card_id` and `former_card_ids`: the
    id is made once, when the card is first written (ADR-016), and a link
    to an id it had before keeps finding it. A first filing gets a new one.

    >>> identity_for("---\\ntype: note\\ncard_id: note-7f3a9c21e4\\n---\\n", "note", "homer/notes/a.md")
    {'card_id': 'note-7f3a9c21e4'}
    """
    try:
        previous = frontmatter_parse(existing or "")
    except FrontmatterError:
        previous = {}
    return card_identity(previous, card_type, path)


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
    identity: dict | None = None,
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
        persons: Ids of the declared people the capture is about.
        tags: List of topic tags.
        model: Model name used for classification, or None.
        capture_id: Stable capture identifier carried on the
            ``dev.famstack.event`` envelope, or None.
        identity: The card's id fields (`identity_for`).

    Returns:
        Frontmatter dict ready for YAML serialization.
    """
    now = (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    # `type` is the OKF-required concept kind (note/bookmark). It is the
    # single home for that distinction; there is no separate `kind` key.
    fm: dict = {"type": kind, **(identity or {}), "title": title}
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


def render_capture(
    *,
    frontmatter: dict,
    kind: str,
    captured_at: str | None,
    source_uri: str | None,
    persons: list[str],
    from_path: str,
    shared_bucket: str,
    summary: str | None = None,
    facts: list | None = None,
    action_items: list | None = None,
    kept_media: dict | None = None,
    caption: str = "",
    replies: "list[tuple[str, str]] | tuple" = (),
    people: list[Person] | None = None,
) -> str:
    """Assemble the mirror file for a bookmark. A note is `render_note`.

    A bookmark differs from a document in two ways:

      1. The meta block uses Captured/Kind/Source instead of the
         document's From/About/Date/Type/Category.
      2. It has no body: the URL plus the summary IS the entry. Action
         items never ride on it (a saved Reddit thread is not a todo).

    Args:
        frontmatter: YAML frontmatter dict.
        kind: "bookmark".
        captured_at: Capture date.
        source_uri: Original source URL, or None.
        persons: The names the classifier gave, for the About line.
        from_path: This entry's own vault path, for relative link math.
        shared_bucket: The institutional bucket slug (for people's cards).
        summary: Prose summary for the briefing callout.
        facts: List of fact strings for the briefing.
        action_items: [] / None for bookmarks.
        kept_media: The archived copy of the uploaded file, as
            ``{"name", "original", "embed"}``. ``original`` is where the
            bytes live in the site; ``embed`` is the version worth
            putting on the page and is empty for anything that should
            be downloaded rather than displayed.
        caption: What the sender wrote with an upload, kept as written.
            The summary is the model's reading of it; this is the record.
        replies: ``(author, words)`` for each reply in the capture's thread,
            in the order they came, kept as written for the same reason.
        people: The declared people the names resolve through.

    Returns:
        Complete markdown string for the mirror file.
    """
    fm_yaml = frontmatter_dump(frontmatter)
    parts = ["---", fm_yaml, "---", ""]

    parts.append(f"# {frontmatter.get('title', 'Untitled')}")
    parts.append("")

    meta_lines: list[str] = []
    if about := About.of(persons, people):
        meta_lines.append("**About** " + about.links(from_path, bucket=shared_bucket))
    line2_bits = []
    if captured_at:
        line2_bits.append(f"**Captured** {captured_at}")
    line2_bits.append(f"**Kind** {kind}")
    meta_lines.append(" · ".join(line2_bits))
    if source_uri:
        meta_lines.append(f"**Source** <{source_uri}>")
    kept = kept_media or {}
    if kept.get("original"):
        # The bytes themselves, kept where the wiki serves them. The
        # Source line above points at the chat message, which is a
        # pointer into a service that need not outlive this entry.
        meta_lines.append(
            f"**File** [{kept.get('name') or 'original'}]({kept['original']})")
    parts.extend(f"> {ln}" for ln in meta_lines)
    parts.append("")

    if briefing := render_briefing(
        summary=summary, facts=facts, action_items=action_items,
    ):
        parts.append(briefing)
        parts.append("")

    # A picture is the entry; a document is a download. Embedding a PDF
    # puts a scrolling frame where a summary should be, so only what
    # reads at a glance goes inline and the rest stays the File link
    # above.
    if kept.get("embed"):
        parts.append(f"![[{kept['embed']}]]")
        parts.append("")

    # The family's own words close the page, so a correction can read
    # them back by their headings (the archivist's `_caption_from`,
    # `_replies_from`). Replies look as they do on a diary card.
    if caption.strip():
        parts.extend(["## Caption", "", caption.strip(), ""])
    if replies:
        parts.extend(["## Replies", ""])
        for who, words in replies:
            parts.append(f"**{who}**")
            parts.extend(f"> {ln}" if ln else ">" for ln in words.strip().splitlines())
            parts.append("")

    return "\n".join(parts)


def render_note(
    *,
    frontmatter: dict,
    sources: Sequence[Source],
    persons: list[str],
    from_path: str,
    shared_bucket: str,
    clean: str | None = None,
    summary: str | None = None,
    facts: list | None = None,
    action_items: list | None = None,
    people: list[Person] | None = None,
) -> str:
    """The card of a note (ADR-018 §2): what it is about, then its sources.

    The title, the meta block, the interpretation, then `## Sources`, one
    blockquote per source in the order of the sidecar's `sources`
    (`stack.cards.sources`). A photo is embedded inside its quote, followed
    by its caption.

    The interpretation is the clean version of the note's words when it
    was filed as its own words (`clean_words`): ``clean`` is a string then,
    "" when there is none worth showing, and the note has no briefing; its
    action items go to the topic's list only. Otherwise (``clean`` None)
    it is the briefing: summary, facts and action items.

    `frontmatter` holds only what the card is about; where it came from
    and how it was made are in the sidecar.
    """
    parts = ["---", frontmatter_dump(frontmatter), "---", "",
             f"# {frontmatter.get('title', 'Untitled')}", ""]
    if about := About.of(persons, people):
        parts += ["> **About** " + about.links(from_path, bucket=shared_bucket), ""]
    if clean is not None:
        if clean.strip():
            parts += [clean.strip(), ""]
    elif briefing := render_briefing(summary=summary, facts=facts, action_items=action_items):
        parts += [briefing, ""]
    return "\n".join([*parts, render_sources(sources), ""])
