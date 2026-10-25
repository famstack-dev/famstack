"""Git mirror — files classified documents and captures into the family memory.

The memory is read and written through its curator, the memory's one
writer (`memory_client`, ADR-019): every filing is one commit there, on
the version of each file it was made from, and kept while Forgejo is down.

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

The deriver compiles a per-entity wiki by globbing the entity's own
tree and grepping the others for cross-references (`persons:`).

The document body is the best representation we have:
  - AI available → LLM-cleaned markdown from `_reformat`
  - AI unavailable → raw Paperless OCR text

Metadata rides in YAML frontmatter (Obsidian/Dataview compatible) plus
a commit trailer `Paperless-Id: N` that enables git-native lookups. A
document whose title changed moves in that same commit, its old path
removed, so its history follows it.

Delete handling is deferred to a future `stack docs reconcile` job —
v1 leaves deleted Paperless docs as stale markdown in git history.

The repo itself (description, README, `<shared_bucket>/`,
`ontology.toml`, `wiki/`) is owned by the memory stacklet's install
pipeline. The archivist writes only to the document and capture
paths above.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from loguru import logger

from stack.cards.fields import FieldOp, FieldValues, apply_ops
from stack.cards.history import CardHistory, Version, history_of
from stack.cards.links import About, correspondent_card, topic_card, without_person_tags
from stack.cards.sources import Source, card_fields, sidecar_path, sidecar_text
from stack.cards import card_id_from
from stack.frontmatter import FrontmatterError
from stack.frontmatter import parse as frontmatter_parse
from stack.tags import card_tags

from .diary_room import household_now

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .vault_context import VaultContext
# A note's sources are read the memory stacklet's way, its old format
# included (ADR-018), against the memory's card schemas.
from stacklets.memory.card_fields import SCHEMA_DIR
from stacklets.memory.note_card import card_sources
# Who a commit to the memory repo is by is the memory stacklet's rule.
# A note's card and sidecar are written by the memory's one card write,
# called here in-process; `stack memory reprocess --apply` calls it on the host.
from .memory_client import CuratorDown, MemoryClient, Refused, put, remove
from .vault_entry import (
    slug,
    capture_hash,
    document_filepath,
    document_resource_url,
    capture_filepath,
    identity_for,
    document_frontmatter,
    capture_frontmatter,
    render_document,
    render_capture,
    render_note,
    render_email_message_section,
    fold_email_message,
)
from stack.lists import (
    CALENDAR,
    LISTS_DIR,
    Item,
    Link,
    ListError,
    action_item,
    calendar_description,
    calendar_slug,
    calendar_title,
    insert_item,
    list_path,
    new_list,
    person_link,
    read_list,
    relink,
    topic_list_description,
)
from stack.people import Person, resolve
from stack.vault import topic_of


# The archivist's account, the author of its own changes (a declared
# topic or correspondent card) and the committer of all of them.
BOT_USERNAME = "archivist-bot"


# ── Dates ────────────────────────────────────────────────────────────────

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _dates(raw) -> list[tuple[str, str]]:
    """The classifier's `dates` as (what, YYYY-MM-DD); anything malformed is left out."""
    out = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        what, day = str(entry.get("what") or "").strip(), str(entry.get("date") or "").strip()
        if what and _ISO_DAY.match(day):
            out.append((what, day))
    return out


def code_commit() -> str:
    """The famstack commit this process runs, as `stack up` stamped it.

    `STACK_COMMIT` is rendered into every stacklet's env, the bot runner's
    included; a process started some other way says `unknown`.
    """
    return os.environ.get("STACK_COMMIT", "").strip() or "unknown"


def _by(submitter: str | None) -> str:
    """Who a capture's commit is by: the family member who filed it (their
    Matrix id or localpart), the archivist when nobody did. The curator
    names them by the memory's rule, as every other writer's commits."""
    return submitter or BOT_USERNAME


def _document_trailers(paperless_id: int, processing: str, model: str | None) -> dict[str, str]:
    """A document commit's trailers: which Paperless document it is and how it was read."""
    return {"Paperless-Id": str(paperless_id), "Processing": processing,
            **({"Model": model} if model else {})}


def _card_id_in(content: str | None) -> str:
    """The card id in a file's frontmatter, "" for none that parses."""
    try:
        return card_id_from(frontmatter_parse(content or ""))
    except FrontmatterError:
        return ""


def _classified_persons(classification: dict) -> list[str]:
    """The names of the people a classification says the record is about."""
    raw = classification.get("persons") or classification.get("person") or []
    if isinstance(raw, str):
        raw = [raw]
    return [p for p in raw if isinstance(p, str) and p]


def _strings(value) -> list[str]:
    """`value` as a list of strings: a classifier may answer one, a list, or null."""
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return [v for v in values if isinstance(v, str) and v.strip()]


def _report_unknown(path: str, about: About) -> None:
    """Log the names no declared person goes by: they stay plain names in `path`."""
    if about.unknown:
        logger.info("[git-mirror] {}: no declared person goes by {}; kept as written",
                    path, ", ".join(about.unknown))


def _filer_localpart(submitter: str | None) -> str | None:
    """The submitter's Matrix localpart, for the `filed_by` frontmatter field."""
    if not submitter:
        return None
    return submitter.split(":")[0].lstrip("@").strip().lower() or None


class Filed(NamedTuple):
    """Where a capture landed in the memory and the card id it carries there.

        filed = await mirror.publish_capture(...)
        filed.path, filed.card_id   # ('marge/notes/2026/10/tent-3a338e.md', 'note-7f3a9c21e4')
    """

    path: str
    card_id: str


class CaptureEntry(NamedTuple):
    """A capture's file as it would be written, and the people it names.

        entry = mirror.capture_entry(target_path=path, existing=text, ...)
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


@dataclass
class GitMirror:
    """Stateful mirror client. One per archivist bot.

    The memory is read and written through `memory`, the archivist's client
    of the curator; its sync calls run in `asyncio.to_thread(...)`.
    `data_dir` keeps which file holds which Paperless document.
    """
    data_dir: Path
    # Slug for the shared/institutional bucket inside the vault. Default
    # "family" matches famstack's stock layout; non-family deployments
    # override via stack.toml [core] shared_bucket → env var
    # SHARED_BUCKET → archivist → here.
    shared_bucket: str = "family"
    paperless_version: str = ""
    # The family's people and calendar, read from the local copy of the
    # family memory; without it, dates go on a calendar started here and
    # name nobody.
    vault: VaultContext | None = None
    memory: MemoryClient = field(default_factory=MemoryClient.from_environ)

    _cache: dict[int, str] = field(default_factory=dict, init=False)
    _cache_loaded: bool = field(default=False, init=False)

    @property
    def cache_path(self) -> Path:
        return self.data_dir / "mirror-cache.json"

    # ── Cache (paperless_id → path) ──────────────────────────────────────

    def _load_cache(self) -> None:
        if self._cache_loaded:
            return
        if self.cache_path.exists():
            try:
                raw = json.loads(self.cache_path.read_text())
                self._cache = {int(k): v for k, v in raw.items()}
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("[git-mirror] Bad cache file ({}), starting empty", e)
                self._cache = {}
        self._cache_loaded = True

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({str(k): v for k, v in self._cache.items()}))
        tmp.replace(self.cache_path)

    async def _lookup_path(self, paperless_id: int) -> str | None:
        """Find the current filepath for a Paperless doc, if any.

        Fast path: cache hit, still in the memory. Cold path: list the
        memory's paths and match the `-p<id>.md` filename suffix.
        """
        self._load_cache()
        cached = self._cache.get(paperless_id)
        if cached:
            if await asyncio.to_thread(self.memory.file, cached):
                return cached
            self._cache.pop(paperless_id, None)

        suffix_variants = (f"-p{paperless_id}.md", f"/p{paperless_id}.md")
        for path in await asyncio.to_thread(self.memory.paths):
            if any(path.endswith(s) or path == s.lstrip("/") for s in suffix_variants):
                self._cache[paperless_id] = path
                self._save_cache()
                return path
        return None

    async def _lookup_capture_path(self, target_path: str) -> str | None:
        """An existing capture with `target_path`'s identity, ignoring its title.

        A capture's filename is `<title-slug>-<hash>.md`; the hash is stable
        per URL/body, the slug is not. So the same link re-pasted with different
        framing, or content re-classified to a different title, would otherwise
        fork a near-duplicate. Match on the `<entity>/<kind>s/` folder + the
        `-<hash>.md` suffix instead: return the exact path when it exists,
        else a same-hash file under a different slug (which the caller then
        updates where it is), else None.
        """
        parts = target_path.split("/")
        if len(parts) < 2:
            return None
        folder = f"{parts[0]}/{parts[1]}"               # <entity>/<kind>s
        suffix = "-" + target_path.rsplit("-", 1)[-1]   # -<hash>.md
        match = None
        for path in await asyncio.to_thread(self.memory.paths, folder):
            if path.endswith(suffix):
                if path == target_path:
                    return path        # exact identity + title → update in place
                match = path           # same identity, different title
        return match

    # ── Filename, frontmatter, body ──────────────────────────────────────

    def _slug(self, text: str) -> str:
        """Filesystem-safe slug. Delegates to ``vault_entry.slug``."""
        return slug(text)

    def _filepath(self, date: str | None, paperless_id: int, title: str | None, has_title: bool) -> str:
        """Document mirror path. Delegates to ``vault_entry.document_filepath``,
        injecting this mirror's configured shared bucket."""
        return document_filepath(self.shared_bucket, date, paperless_id, title, has_title)

    def _frontmatter(
        self,
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
        identity: dict | None = None,
    ) -> dict:
        """Document frontmatter. Delegates to ``vault_entry.document_frontmatter``,
        injecting this mirror's known Paperless server version."""
        fm = document_frontmatter(
            title=title, date=date,
            correspondent=correspondent, document_type=document_type,
            category=category, persons=persons, tags=tags,
            paperless_id=paperless_id, paperless_url=paperless_url,
            processing=processing, model=model,
            paperless_version=self.paperless_version,
            identity=identity,
        )
        return fm

    def _render(
        self,
        *,
        frontmatter: dict,
        body: str,
        correspondent: str | None,
        persons: list[str],
        from_path: str,
        summary: str | None = None,
        facts: list | None = None,
        action_items: list | None = None,
        source_link: tuple[str, str] | None = None,
        people: list[Person] | None = None,
    ) -> str:
        """Document mirror markdown. Delegates to ``vault_entry.render_document``."""
        return render_document(
            frontmatter=frontmatter, body=body,
            correspondent=correspondent, persons=persons,
            from_path=from_path, shared_bucket=self.shared_bucket,
            summary=summary, facts=facts, action_items=action_items,
            source_link=source_link, people=people,
        )

    def _render_capture(
        self, *,
        frontmatter: dict,
        kind: str,
        captured_at: str | None,
        source_uri: str | None,
        persons: list[str],
        from_path: str,
        summary: str | None = None,
        facts: list | None = None,
        action_items: list | None = None,
        kept_media: dict | None = None,
        caption: str = "",
        replies: "list[tuple[str, str]] | tuple" = (),
        people: list[Person] | None = None,
    ) -> str:
        """Bookmark mirror markdown. Delegates to ``vault_entry.render_capture``."""
        return render_capture(
            frontmatter=frontmatter, kind=kind,
            captured_at=captured_at, source_uri=source_uri, persons=persons,
            from_path=from_path, shared_bucket=self.shared_bucket,
            summary=summary, facts=facts, action_items=action_items,
            kept_media=kept_media, caption=caption, replies=replies,
            people=people,
        )

    def _topic_of_capture(self, target_path: str) -> str:
        """The topic slug a capture was filed under, or "" outside a shared topic."""
        parts = target_path.split("/")
        if len(parts) < 3 or parts[0] != self.shared_bucket:
            return ""
        topic_slug = parts[1]
        if topic_slug in {"documents", "notes", "bookmarks", "emails", "_unfiled", LISTS_DIR}:
            return ""
        return topic_slug

    async def _fold_action_items_into_list(
        self,
        *,
        topic: str,
        topic_name: str = "",
        record_path: str,
        record_title: str,
        action_items: list | None,
        by: str,
        previous_path: str = "",
        today: str = "",
    ) -> bool:
        """Put a record's action items on its topic's list, `lists/<topic>.md`.

        The list is started when the topic has none, titled with the
        topic's name as classified (`topic_name`, "Haus & Garten"), else
        with its slug made readable. Each item links the record it came
        from and goes on as `_put_on_list` says. An item due before
        `today` (the household's date when not given) stays off the list:
        an old letter filed late, or a refile of last year's bill, has
        nothing left to do. The record's summary still names it.
        """
        source = Link(record_title, record_path) if record_path else None
        today = today or household_now().date().isoformat()
        items = [i for ai in action_items or [] if (i := action_item(ai, source=source))
                 and (not i.due or i.due >= today)]
        if not (topic and items):
            return False
        title = topic_name or topic.replace("-", " ").title()
        path = list_path(topic, bucket=self.shared_bucket)
        return await self._put_on_list(
            path=path,
            start=new_list(title, path=path, topics=[topic], description=topic_list_description(
                title, os.environ.get("LANGUAGE", ""))),
            items=items, record_path=record_path, previous_path=previous_path,
            title=f"add action items to {topic}", by=by,
        )

    async def _put_on_list(
        self,
        *,
        path: str,
        start: str,
        items: list[Item],
        record_path: str,
        previous_path: str,
        title: str,
        by: str,
    ) -> bool:
        """Add a record's `items` to the list at `path`, started as `start` if missing.

        A record's items go on the list once: when the list already has
        items from it, a refile adds none, because the model words them
        differently each time and the family may have ticked, reworded or
        removed them since. When a refile renamed the record
        (`previous_path`), those items are linked to its new path instead.
        An item already on the list is left alone: the same text, and on a
        calendar the same date too, since the school's Elternabend comes
        round again. The list is written on the version it was read at: a
        tick made meanwhile keeps the items off it this time, and is kept.
        """
        try:
            existing = await asyncio.to_thread(self.memory.file, path)
            prior = existing.text if existing else ""
            content = prior or start
            if previous_path and previous_path != record_path:
                content = relink(content, previous_path, record_path, path=path)
            lst = read_list(content, path=path)

            def key(i: Item) -> tuple[str, str]:
                return i.text.casefold(), i.due if lst.kind == CALENDAR else ""
            already = {s for i in lst.items for s in i.sources}
            for item in items if record_path not in already else []:
                if key(item) not in {key(i) for i in read_list(content, path=path).items}:
                    content = insert_item(content, item, path=path)
            if content == prior:
                return False
            await asyncio.to_thread(
                self.memory.commit, [put(path, content, base=existing.version if existing else "")],
                by=by, kind="lists", title=title)
            return True
        except ListError as e:
            logger.warning("[git-mirror] {} is not a list, items not added: {}", path, e)
            return False
        except (Refused, CuratorDown) as e:
            logger.warning("[git-mirror] Items not added to {}: {}", path, e)
            return False

    async def _fold_document_todos(
        self,
        *,
        classification: dict,
        record_path: str,
        record_title: str,
        previous_path: str = "",
        today: str = "",
    ) -> None:
        topics = classification.get("topics") or classification.get("topic") or []
        if isinstance(topics, str):
            topics = [topics]
        for topic in topics:
            if not isinstance(topic, str) or not topic.strip():
                continue
            await self._fold_action_items_into_list(
                topic=self._slug(topic),
                topic_name=topic.strip(),
                record_path=record_path,
                record_title=record_title,
                previous_path=previous_path,
                action_items=classification.get("action_items") or [],
                by=BOT_USERNAME,
                today=today,
            )

    async def _fold_document_dates(
        self,
        *,
        classification: dict,
        record_path: str,
        record_title: str,
        previous_path: str = "",
        today: str = "",
    ) -> bool:
        """Put a document's dates on the family's calendar.

        Each entry is assigned to the people the document names, as far
        as the family has declared them, and links the document. The
        calendar is the family's list of `kind: calendar` (`VaultContext`),
        started when there is none. A date already past stays off, as a
        past action item does.
        """
        today = today or household_now().date().isoformat()
        people = self._person_links(classification)
        source = Link(record_title, record_path)
        entries = [Item(what, due=day, links=(*people, source))
                   for what, day in _dates(classification.get("dates")) if day >= today]
        if not entries:
            return False
        language = os.environ.get("LANGUAGE", "")
        path, title = (self.vault.calendar() if self.vault else None) or \
            (list_path(calendar_slug(language), bucket=self.shared_bucket), calendar_title(language))
        return await self._put_on_list(
            path=path,
            start=new_list(title, path=path, description=calendar_description(language),
                           kind=CALENDAR),
            items=entries, record_path=record_path, previous_path=previous_path,
            title="add dates to the calendar", by=BOT_USERNAME,
        )

    def _person_links(self, classification: dict) -> tuple[Link, ...]:
        """Links to the declared people a classification names; unknown names are left out."""
        names = classification.get("persons") or []
        known = (self.vault.people() if self.vault else None) or []
        found = [p for n in ([names] if isinstance(names, str) else names)
                 if isinstance(n, str) and (p := resolve(n, known))]
        return tuple(person_link(p.id, p.first_name, bucket=self.shared_bucket)
                     for p in dict.fromkeys(found))

    async def _fold_capture_todos(
        self,
        *,
        target_path: str,
        classification: dict,
        by: str,
        previous_path: str = "",
        today: str = "",
    ) -> None:
        topic = self._topic_of_capture(target_path)
        await self._fold_action_items_into_list(
            topic=topic,
            record_path=target_path,
            previous_path=previous_path,
            record_title=str(classification.get("title") or "").strip()
            or Path(target_path).stem,
            action_items=classification.get("action_items") or [],
            by=by,
            today=today,
        )

    # ── Publish ──────────────────────────────────────────────────────────

    # ── The cards a record links ─────────────────────────────────────────
    #
    # A record links the cards of the people, the correspondent and the
    # topic it names (ADR-017 §3, §10). People are declared by the family;
    # a correspondent's card and a topic's are written here the first time
    # a record names them, and never rewritten: from then on they are the
    # family's (`stack.cards.links`).

    def _people(self) -> list[Person]:
        """The declared people names resolve through; none without a local copy."""
        return (self.vault.people() if self.vault else None) or []

    def _tags(self, tags: list[str] | None) -> list[str]:
        """The tags a record is written with: topics by the ontology's id, the
        rest as keywords, no `Person:` tags (ADR-017 §4, `stack.tags`)."""
        if self.vault:
            ontology = self.vault.ontology()
        else:
            from stacklets.memory.lib import get_ontology
            ontology = get_ontology()
        return card_tags(without_person_tags(tags or []), ontology)

    async def _declare(self, path: str, text: str) -> None:
        """Write the card at `path` when the memory has none there yet.

        Created, never updated: the write names no version, so a card that
        appeared meanwhile makes the curator refuse it, and the card that
        is there stays.
        """
        try:
            if await asyncio.to_thread(self.memory.file, path):
                return
            await asyncio.to_thread(self.memory.commit, [put(path, text, base="")],
                                    by=BOT_USERNAME, kind="declare", title=path)
        except Refused as e:
            if e.reason != "changed":
                logger.warning("[git-mirror] could not declare {}: {}", path, e)
            return
        except CuratorDown as e:
            logger.warning("[git-mirror] could not declare {}: {}", path, e)
            return
        logger.info("[git-mirror] declared {}", path)

    async def _declare_topic(self, record_path: str) -> None:
        """Give the topic a record was filed into its card, the first time."""
        if found := topic_of(record_path):
            await self._declare(*topic_card(*found))

    async def publish(
        self,
        *,
        paperless_id: int,
        classification: dict,
        body_text: str,
        processing: str,
        model: str | None,
        paperless_url: str,
        tags: list[str] | None = None,
        fallback_title: str | None = None,
        summary: str | None = None,
    ) -> bool:
        """Create or update a document file in the git mirror.

        Returns True on success, False if skipped or failed. Failures are
        logged but never raised — the mirror is best-effort.

        When classification produced no title (LLM flake, disabled, etc.)
        the caller can supply `fallback_title` — typically the original
        filename. Results in a far friendlier Obsidian entry than
        `Paperless #42`.
        """
        # Title comes from AI classification first, then caller's fallback
        # (usually the original filename), and only then the generic
        # `Paperless #N`. A non-generic title is what gates the slug-style
        # filename — *not* whether the body was AI-reformatted. Text files
        # with `processing=original` still get a readable slug.
        resolved_title = classification.get("title") or fallback_title
        title = resolved_title or f"Paperless #{paperless_id}"
        date = classification.get("date")
        correspondent = classification.get("correspondent")
        document_type = classification.get("document_type")

        topics = classification.get("topics") or classification.get("topic") or []
        if isinstance(topics, str):
            topics = [topics]
        category = topics[0] if topics else None

        persons = _classified_persons(classification)
        people = self._people()
        about = About.of(persons, people)

        target_path = self._filepath(date, paperless_id, title, bool(resolved_title))

        try:
            existing_path = await self._lookup_path(paperless_id)
            existing = await asyncio.to_thread(self.memory.file, existing_path) \
                if existing_path else None
        except (Refused, CuratorDown) as e:
            logger.warning("[git-mirror] Publish failed for paperless #{}: {}", paperless_id, e)
            return False

        fm = self._frontmatter(
            title=title, date=date,
            correspondent=correspondent, document_type=document_type,
            category=category, persons=about.ids, tags=self._tags(tags),
            paperless_id=paperless_id, paperless_url=paperless_url,
            processing=processing, model=model,
            identity=identity_for(existing.text if existing else None, "document", target_path),
        )

        # The briefing's "Show Document" link wants the per-document
        # details page, not the instance root the caller hands us — the
        # same URL stamped into the frontmatter `resource` field.
        paperless_doc_url = document_resource_url(paperless_url, paperless_id)

        # Briefing block inputs come straight from the classifier. The
        # `summary` parameter on `publish()` is the multi-section
        # Markdown that goes into Paperless and the commit body — it
        # already contains its own `## Summary` / `## Facts` headings,
        # so feeding it into `render_briefing` (which would wrap it in
        # ANOTHER `## Summary`) double-nests headings and duplicates the
        # facts list. The briefing block expects prose; that lives on
        # `classification["summary"]`.
        briefing_summary = classification.get("summary")
        briefing_facts = classification.get("facts") or []
        briefing_actions = classification.get("action_items") or []

        source_link: tuple[str, str] | None = None
        if paperless_doc_url:
            source_link = ("Show Document", paperless_doc_url)

        content = self._render(
            frontmatter=fm, body=body_text,
            correspondent=correspondent, persons=persons, people=people,
            from_path=target_path,
            summary=briefing_summary,
            facts=briefing_facts,
            action_items=briefing_actions,
            source_link=source_link,
        )

        # A classifier summary rides in the commit body: `git log` on the
        # memory reads as an archive log, and `git log --grep` searches it.
        verb = "update" if existing else "learn"
        if existing and existing.path != target_path:
            changes = [remove(existing.path, base=existing.version), put(target_path, content, base="")]
        else:
            changes = [put(target_path, content, base=existing.version if existing else "")]
        try:
            await asyncio.to_thread(
                self.memory.commit, changes, by=BOT_USERNAME, kind=verb, title=title,
                body=summary or "", trailers=_document_trailers(paperless_id, processing, model))
        except (Refused, CuratorDown) as e:
            logger.warning("[git-mirror] Publish failed for paperless #{}: {}", paperless_id, e)
            return False

        self._cache[paperless_id] = target_path
        self._save_cache()
        _report_unknown(target_path, about)
        if correspondent:
            await self._declare(*correspondent_card(
                correspondent, _strings(classification.get("correspondent_aliases")),
                bucket=self.shared_bucket))
        await self._fold_document_todos(classification=classification,
                                        record_path=target_path, record_title=title,
                                        previous_path=existing_path or "")
        await self._fold_document_dates(classification=classification,
                                        record_path=target_path, record_title=title,
                                        previous_path=existing_path or "")
        logger.info("[git-mirror] {} #{} → {}", verb, paperless_id, target_path)
        return True

    # ── Captures ─────────────────────────────────────────────────────────
    #
    # A "capture" is a non-Paperless source filed in the same vault.
    # Producers today:
    #   - URL paste: trafilatura extracts a web article into Markdown,
    #     classifier produces a digest, result is filed as kind=bookmark.
    #   - Text paste: the user's typed body, classifier produces a digest,
    #     result is filed as kind=note.
    #
    # Both land under the sender's entity bucket:
    #
    #   <entity>/notes/YYYY/MM/<slug>-<hash>.md       (kind=note)
    #   <entity>/bookmarks/YYYY/MM/<slug>-<hash>.md   (kind=bookmark)
    #
    # `<entity>` is the Matrix localpart of the sender (lowercased):
    # @homer:test.local → `homer/`. Routing by sender matches intent —
    # Homer chose to save this — and makes per-entity wiki compilation
    # a single recursive glob. Cross-mentions (a Homer-authored note
    # about Bart) stay under Homer; the `persons:` frontmatter indexes
    # them for Bart's wiki compile.
    #
    # The hash is a short prefix of sha256(hash_key). Two purposes:
    #   - Re-paste the same source → same path → idempotent update,
    #     not a duplicate file.
    #   - Two different sources with the same title on the same day →
    #     still resolve to different filenames.
    #
    # Frontmatter shape: `kind: bookmark|note`, optional `source_uri`.
    # Paperless fields (paperless_id, paperless_url) are absent so
    # Obsidian/Dataview queries can still tell capture-sourced entries
    # from Paperless-sourced ones at a glance.

    _CAPTURE_HASH_LEN = 6

    def _capture_filepath(
        self, *,
        entity: str,
        kind: str,
        captured_at: str,
        title: str | None,
        hash_key: str,
    ) -> str:
        """Capture mirror path. Delegates to ``vault_entry.capture_filepath``,
        injecting this mirror's capture-hash length.

        Email threads are date-prefixed so the month folder sorts
        chronologically (like documents); notes/bookmarks keep the plain
        slug name.
        """
        return capture_filepath(
            entity, kind, captured_at, title, hash_key, self._CAPTURE_HASH_LEN,
            date_prefix=(kind == "email"),
        )

    def _capture_frontmatter(
        self, *,
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
        """Capture frontmatter. Delegates to ``vault_entry.capture_frontmatter``.

        A note's keeps only what the card is about; where it came from
        goes into its sidecar (ADR-018 §3, `stack.cards.sources`).
        """
        fm = capture_frontmatter(
            title=title, captured_at=captured_at, kind=kind,
            source_uri=source_uri, persons=persons, tags=tags,
            model=model, capture_id=capture_id, filed_by=filed_by,
            identity=identity,
        )
        if kind == "note":
            fm = card_fields(fm)
        return fm

    async def card_history(self, card_id: str) -> CardHistory | None:
        """What a refile of the note `card_id` applies again of its history
        (ADR-018 §7), as the curator reads it across the card's moves. None
        when no card carries the id or the curator cannot be reached."""
        try:
            found = await asyncio.to_thread(self.memory.versions, card_id)
        except (Refused, CuratorDown) as e:
            logger.warning("[git-mirror] history of {} not read: {}", card_id, e)
            return None
        if found is None:
            return None
        return history_of([Version.from_json(v) for v in found.versions], read_sources=card_sources)

    def _field_values(self, people: list[Person]) -> FieldValues:
        """What a field edit may name here: the card schemas, the declared
        people, the ontology."""
        if self.vault:
            ontology = self.vault.ontology()
        else:
            from stacklets.memory.lib import get_ontology
            ontology = get_ontology(None)
        return FieldValues(schemas=SCHEMA_DIR, person_ids=frozenset(p.id for p in people),
                           ontology=ontology)

    async def read_capture(self, path: str) -> str | None:
        """Return the raw markdown of a capture entry, or None if missing.

        Used by ``CapturePipeline.reprocess`` to re-classify an
        already-filed capture without re-fetching the original binary.
        Best-effort: a curator that cannot answer is logged and surfaced
        as None so the caller can render a friendly "couldn't find it" reply.
        """
        try:
            found = await asyncio.to_thread(self.memory.file, path)
        except (Refused, CuratorDown) as e:
            logger.warning("[git-mirror] read_capture {} failed: {}", path, e)
            return None
        return found.text if found else None

    def capture_entry(
        self, *,
        target_path: str,
        existing: str | None,
        kind: str,
        source_uri: str | None,
        title_hint: str | None,
        body_text: str,
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
    ) -> "CaptureEntry":
        """The files a capture is written as at `target_path`, without writing them.

        ``existing`` is the file there now, whose card id the entry keeps
        (`identity_for`). `publish_capture` writes what this returns; a
        preview of a refile shows it (`stack memory reprocess`). The other
        arguments are `publish_capture`'s. A note's entry carries its
        sidecar too, and a person's field edits ``ops`` applied again to its
        card, in order (ADR-018 §7); one that no longer fits is left out
        and named in `CaptureEntry.left_out`.
        """
        title = classification.get("title") or title_hint or "Capture"
        persons = _classified_persons(classification)
        people = self._people()
        about = About.of(persons, people)
        fm = self._capture_frontmatter(
            title=title,
            captured_at=captured_at,
            kind=kind,
            source_uri=source_uri,
            persons=about.ids,
            tags=self._tags(tags),
            model=model,
            capture_id=capture_id,
            filed_by=_filer_localpart(submitter),
            identity=identity_for(existing, kind, target_path),
        )
        if kind == "note":
            card = render_note(
                frontmatter=fm, sources=sources, persons=persons, people=people,
                from_path=target_path, shared_bucket=self.shared_bucket, clean=clean,
                summary=classification.get("summary"),
                facts=classification.get("facts") or [],
                action_items=classification.get("action_items") or [],
            )
            left_out: list[str] = []
            if ops:
                card, left_out = apply_ops(card, ops, values=self._field_values(people),
                                           path=target_path, bucket=self.shared_bucket)
                fm = frontmatter_parse(card)
            return CaptureEntry(fm, card, about, sidecar_text(
                card_id=str(fm.get("card_id") or ""), filed_by=_filer_localpart(submitter) or "",
                sources=sources, made=made or {}), tuple(left_out))
        content = self._render_capture(
            frontmatter=fm,
            kind=kind,
            captured_at=captured_at,
            source_uri=source_uri,
            persons=persons,
            people=people,
            from_path=target_path,
            summary=classification.get("summary"),
            facts=classification.get("facts") or [],
            # Action items only when the classifier extracted them — notes
            # that opted in (kind == "note"); bookmarks never carry the field.
            action_items=classification.get("action_items") or [],
            kept_media=kept_media,
            caption=caption,
            replies=replies,
        )
        return CaptureEntry(fm, content, about)

    async def publish_capture(
        self, *,
        entity: str,
        kind: str,
        source_uri: str | None,
        title_hint: str | None,
        body_text: str,
        classification: dict,
        captured_at: str,
        model: str | None,
        tags: list[str] | None = None,
        existing_path: str | None = None,
        capture_id: str | None = None,
        submitter: str | None = None,
        kept_media: dict | None = None,
        caption: str = "",
        replies: "list[tuple[str, str]] | tuple" = (),
        clean: str | None = None,
        sources: "Sequence[Source]" = (),
        made: dict | None = None,
        ops: "Sequence[FieldOp]" = (),
        changed_by: str | None = None,
    ) -> Filed | None:
        """Create or update a capture entry in the mirror.

        `entity` is the sender's slug — the Matrix localpart, lowercased.
        Routes the capture under `<entity>/<kind>s/...`.

        `kind` is "bookmark" (URL pointer + LLM summary; body usually
        empty) or "note" (pasted body the user typed; body preserved).
        Caller decides what to pass as `body_text` — for bookmarks in
        archival mode, that's the extracted Markdown; for bookmarks in
        marker-only mode, "".

        Identity for idempotent updates: `source_uri` when present
        (re-pastes of the same URL update the same file), otherwise
        the body text (re-pastes of the same text update; edits create
        a new file).

        ``kept_media`` is the archived copy of the uploaded file, when
        the caller kept one: the entry names it and, for a picture,
        shows it. Absent on every capture that has no bytes of its own
        (a URL, a pasted note) and on a reprocess, which re-renders
        from the prior entry and never sees the original again.

        ``caption`` is what the sender wrote with an upload, and
        ``replies`` the ``(author, words)`` written in its thread; the
        entry keeps both word for word.

        A note is written as its card and its sidecar, in one commit
        (ADR-018): ``sources`` are what it was made from, in order, and
        ``made`` how it was made. ``clean`` is the clean version of its
        words when it was filed as its own words (`vault_entry.render_note`).
        ``body_text`` then only names it, for its path. ``ops`` are a
        person's field edits, applied again to a refiled note (`capture_entry`).

        ``existing_path`` is the reprocess hook: the entry is rewritten
        where it is, whatever its new title.

        Returns where the capture landed (its path relative to the repo
        root) and its card id on success, None when the curator cannot be
        reached, the write failed, or a note changed after it was read
        (nothing is overwritten then). Failures are logged but never
        raised: captures are best-effort.
        """
        resolved_title = classification.get("title") or title_hint
        title = resolved_title or "Capture"

        hash_key = source_uri or body_text
        target_path = self._capture_filepath(
            entity=entity,
            kind=kind,
            captured_at=captured_at,
            title=title if resolved_title else None,
            hash_key=hash_key,
        )

        # Dedup on identity, not filename: if this URL/body already lives under
        # a different title (a re-paste with new framing, an older differently-
        # titled capture), find it by its hash so we update that entry instead
        # of forking a duplicate. The reprocess caller already knows the path,
        # so only look when it didn't pass one.
        try:
            if existing_path is None:
                existing_path = await self._lookup_capture_path(target_path)
            # A capture keeps the path it was first filed under. Topic pages,
            # list items and the link in the chat point at it, so a new title
            # or a correction from someone else in the room changes what the
            # entry says, never where it is.
            if existing_path:
                target_path = existing_path
            existing = await asyncio.to_thread(self.memory.file, target_path)
            side = await asyncio.to_thread(self.memory.file, sidecar_path(target_path)) \
                if kind == "note" else None
        except (Refused, CuratorDown) as e:
            logger.warning("[git-mirror] Capture not filed for {}: {}", source_uri or "(paste)", e)
            return None

        # The filer stays the card's `filed_by` (`submitter`). The commit is
        # by whoever made this change: on a refile from a reply in the chat
        # that is the person who replied (`changed_by`), so `git log
        # --author` and the dashboard name them, and the subject says it was
        # a reply. Committer stays the bot (ADR-015).
        by = _by(changed_by or submitter)
        entry = self.capture_entry(
            target_path=target_path, existing=existing.text if existing else None,
            kind=kind, source_uri=source_uri, title_hint=title_hint,
            body_text=body_text, classification=classification,
            captured_at=captured_at, model=model, tags=tags,
            capture_id=capture_id, submitter=submitter, kept_media=kept_media,
            caption=caption, replies=replies, clean=clean, sources=sources, made=made, ops=ops,
        )
        fm, content, about = entry.frontmatter, entry.content, entry.about
        for line in entry.left_out:
            logger.warning("[git-mirror] {}: a field edit no longer fits, left out: {}",
                           target_path, line)
        briefing_summary = classification.get("summary")

        # A note is its card and its sidecar (ADR-018), written in one
        # commit, each on the version it was read at, so neither changes
        # without the other.
        verb = ("reply" if changed_by else "update") if existing else "capture"
        changes = [put(target_path, content, base=existing.version if existing else "")]
        if kind == "note":
            changes.append(put(sidecar_path(target_path), entry.sidecar,
                               base=side.version if side else ""))
        trailers = {"Source": source_uri or "(paste)", **({"Model": model} if model else {})}
        try:
            await asyncio.to_thread(self.memory.commit, changes, by=by, kind=verb, title=title,
                                    body=briefing_summary or "", trailers=trailers)
        except Refused as e:
            if e.reason == "changed":
                logger.warning("[git-mirror] Capture not written, nothing overwritten: {}", e)
            else:
                logger.warning("[git-mirror] Capture publish failed for {}: {}",
                               source_uri or "(paste)", e)
            return None
        except CuratorDown as e:
            logger.warning("[git-mirror] Capture publish failed for {}: {}",
                           source_uri or "(paste)", e)
            return None

        logger.info("[git-mirror] {} → {}", verb, target_path)
        _report_unknown(target_path, about)
        await self._declare_topic(target_path)
        await self._fold_capture_todos(
            target_path=target_path,
            classification=classification,
            by=by,
            previous_path=existing_path or "",
        )
        return Filed(target_path, str(fm.get("card_id") or ""))

    # ── Email threads ─────────────────────────────────────────────────────
    #
    # Email is the one capture source that accumulates: every message in a
    # conversation folds into a single file keyed by the thread root, so the
    # whole exchange reads top to bottom in one place. New messages append a
    # dated section; re-folding a message already present is a no-op (its
    # `mid:` marker is detected). The path lives under the routed bucket:
    #
    #   <bucket>/emails/YYYY/MM/<subject-slug>-<thread-hash>.md
    #
    # The shape (frontmatter, sections, markers) is owned by vault_entry;
    # this method is just the read-fold-write around it, through the curator.

    async def _find_email_thread(self, entity: str, digest: str) -> str | None:
        """The existing thread file for this digest, or None.

        Lists the entity's emails for a ``…-<digest>.md`` file. The digest
        is over the thread root, stable across the conversation, so this
        returns the file the first message created — replies append to it
        even when their title (and slug) has drifted.
        """
        suffix = f"-{digest}.md"
        return next((path for path in await asyncio.to_thread(self.memory.paths, f"{entity}/emails")
                     if path.endswith(suffix)), None)

    async def publish_email_message(
        self, *,
        entity: str,
        thread_uri: str | None,
        message_id: str | None,
        from_addr: str | None,
        title_hint: str | None,
        body_text: str,
        classification: dict,
        captured_at: str,
        model: str | None,
        tags: list[str] | None = None,
        capture_id: str | None = None,
    ) -> Filed | None:
        """Fold one email message into its thread file.

        `entity` is the routed bucket (mailbox binding) or the sender's
        slug. `thread_uri` is the ``mid:`` URI of the thread root — it keys
        the file, so every reply lands in the same entry. Returns the path
        the thread lives at and its card id (even when the message was
        already folded, an idempotent no-op), or None when the curator is
        unreachable or the write failed. Best-effort like the rest of the mirror.
        """
        resolved_title = classification.get("title") or title_hint
        title = resolved_title or "Email thread"

        persons = _classified_persons(classification)
        people = self._people()
        about = About.of(persons, people)

        # Thread files are keyed by the thread-root hash, but the slug comes
        # from the per-message title, which drifts ("Dental appointment…"
        # vs a reply's "…fasting inquiry"). So look up an existing thread
        # file by its hash suffix and append there, instead of spawning a
        # new file per reply. Mirrors how the document mirror reuses a
        # renamed doc's `-p<id>.md` path.
        digest = capture_hash(thread_uri or body_text, self._CAPTURE_HASH_LEN)
        try:
            target_path = await self._find_email_thread(entity, digest)
            if target_path is None:
                target_path = self._capture_filepath(
                    entity=entity,
                    kind="email",
                    captured_at=captured_at,
                    title=title if resolved_title else None,
                    hash_key=thread_uri or body_text,
                )
            existing = await asyncio.to_thread(self.memory.file, target_path)
        except (Refused, CuratorDown) as e:
            logger.warning("[git-mirror] Email fold failed for {}: {}", thread_uri or "(no thread)", e)
            return None
        existing_content = (existing.text if existing else None) or None

        section = render_email_message_section(
            message_id=message_id,
            from_addr=from_addr,
            captured_at=captured_at,
            body=body_text,
            summary=classification.get("summary"),
            facts=classification.get("facts") or [],
            action_items=classification.get("action_items") or [],
        )

        tags = self._tags(tags)
        new_fm = self._capture_frontmatter(
            title=title,
            captured_at=captured_at,
            kind="email",
            source_uri=thread_uri,
            persons=about.ids,
            tags=tags,
            model=model,
            capture_id=capture_id,
            identity=identity_for(existing_content, "email", target_path),
        )

        try:
            content = fold_email_message(
                existing_content,
                section=section,
                message_id=message_id,
                new_frontmatter=new_fm,
                title=title,
                captured_at=captured_at,
                source_uri=thread_uri,
                persons=persons,
                tags=tags,
                from_path=target_path,
                shared_bucket=self.shared_bucket,
                people=people,
            )
        except FrontmatterError as e:
            # Merging into a header that does not parse would rewrite the
            # thread without its type and title; the file stays as it is.
            logger.warning(
                "[git-mirror] {}: frontmatter does not parse ({}), email not folded",
                target_path, e,
            )
            return None
        if content is None:
            # Already folded into this thread — nothing to write.
            logger.info("[git-mirror] email already folded → {}", target_path)
            return Filed(target_path, _card_id_in(existing_content))

        verb = "update" if existing_content else "capture"
        trailers = {"Thread": thread_uri or "(none)",
                    **({"Message-Id": message_id} if message_id else {}),
                    **({"Model": model} if model else {})}
        try:
            await asyncio.to_thread(
                self.memory.commit, [put(target_path, content, base=existing.version if existing else "")],
                by=BOT_USERNAME, kind=verb, title=title,
                body=classification.get("summary") or "", trailers=trailers)
        except (Refused, CuratorDown) as e:
            logger.warning(
                "[git-mirror] Email fold failed for {}: {}",
                thread_uri or "(no thread)", e,
            )
            return None

        logger.info("[git-mirror] {} email → {}", verb, target_path)
        _report_unknown(target_path, about)
        await self._declare_topic(target_path)
        await self._fold_capture_todos(
            target_path=target_path,
            classification=classification,
            by=BOT_USERNAME,
        )
        return Filed(target_path, _card_id_in(content))
