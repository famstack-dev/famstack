"""Git mirror — publishes classified Paperless documents to Forgejo.

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
a commit trailer `Paperless-Id: N` that enables git-native lookups.

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
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from stack.forgejo import ForgejoClient, ForgejoError

from vault_entry import (
    slug,
    document_filepath,
    document_resource_url,
    capture_filepath,
    document_frontmatter,
    capture_frontmatter,
    render_document,
    render_capture,
)


# The shared family knowledge vault. The memory stacklet creates and
# seeds the org + repo (description, README, ontology, facts); the
# archivist only writes Markdown under entity-rooted paths (see module
# docstring), and skips mirroring entirely if memory hasn't provisioned
# the repo yet.
REPO_NAME = "memory"

BOT_USERNAME = "archivist-bot"
BOT_EMAIL = "archivist-bot@local"
TOKEN_NAME = "archivist-git-mirror"
TOKEN_SCOPES = ["write:repository", "read:repository", "read:user"]


@dataclass
class MirrorCreds:
    """archivist-bot's Forgejo password + token.

    Persisted to the bot's data dir, regenerated only on first setup.
    """
    password: str
    token: str


@dataclass
class GitMirror:
    """Stateful mirror client. One per archivist bot.

    Forgejo I/O goes through the framework's sync `ForgejoClient`
    wrapped in `asyncio.to_thread(...)` — strictly simpler than
    maintaining a parallel aiohttp client with the same surface, and
    the same module backs the code-stacklet CLI plugins.
    """
    code_url: str
    admin_user: str
    admin_password: str
    admin_usernames: list[str]
    data_dir: Path
    org_name: str = "family"
    # Slug for the shared/institutional bucket inside the vault. Default
    # "family" matches famstack's stock layout; deskstack or non-family
    # deployments override via stack.toml [core] shared_bucket → env
    # var SHARED_BUCKET → archivist → here.
    shared_bucket: str = "family"
    paperless_version: str = ""

    _setup_done: bool = field(default=False, init=False)
    _creds: MirrorCreds | None = field(default=None, init=False)
    _cache: dict[int, str] = field(default_factory=dict, init=False)
    _cache_loaded: bool = field(default=False, init=False)

    @property
    def repo_owner(self) -> str:
        """The Forgejo login that owns the documents repo. Equals the
        configured org — publishes, tree walks, and commit fetches all
        go through this namespace."""
        return self.org_name

    @property
    def creds_path(self) -> Path:
        return self.data_dir / "forgejo-creds.json"

    @property
    def cache_path(self) -> Path:
        return self.data_dir / "mirror-cache.json"

    # ── Setup (idempotent, lazy) ─────────────────────────────────────────

    async def ensure_setup(self) -> bool:
        """Ensure the archivist-bot can push to the memory-provisioned repo.

        Sets up docs' own resources — the archivist-bot user, its push
        token, and the bot's membership in the family org Owners team.
        Creation of the org, repo, and seeds belongs to the memory
        stacklet; if memory hasn't provisioned the repo yet, this skips.

        Returns True if setup succeeded (or was already done), False if
        Forgejo is unreachable or the repo isn't provisioned yet.
        Subsequent calls short-circuit once `_setup_done` is set.
        """
        if self._setup_done:
            return True

        client = ForgejoClient(
            url=self.code_url,
            admin_user=self.admin_user,
            admin_password=self.admin_password,
        )

        if not await asyncio.to_thread(client.ping):
            logger.info("[git-mirror] Forgejo unreachable at {}, skipping", self.code_url)
            return False

        self._creds = self._load_or_create_creds()
        try:
            await asyncio.to_thread(
                client.create_user, BOT_USERNAME, BOT_EMAIL, self._creds.password,
            )
        except ForgejoError as e:
            logger.warning("[git-mirror] Could not ensure bot user: {}", e)
            return False

        if not self._creds.token:
            try:
                token = await asyncio.to_thread(
                    client.issue_token,
                    BOT_USERNAME, self._creds.password, TOKEN_NAME, TOKEN_SCOPES,
                )
                self._creds.token = token
                self._save_creds()
            except ForgejoError as e:
                logger.warning("[git-mirror] Could not issue token: {}", e)
                return False

        # The memory stacklet owns creation of the family org, the
        # family/memory repo, and its seeds. If memory hasn't provisioned
        # the repo yet there is nowhere to mirror — skip best-effort and
        # let the next document retry once memory is up. The document
        # still files into Paperless either way.
        repo = await asyncio.to_thread(client.get_repo, self.org_name, REPO_NAME)
        if repo is None:
            logger.info(
                "[git-mirror] {}/{} not provisioned by the memory stacklet yet; "
                "skipping mirror",
                self.org_name, REPO_NAME,
            )
            return False

        # ── Team membership ──────────────────────────────────────────
        # Grant the bot push access via the org Owners team. Org/team ops
        # run as the admin (which owns the org through memory's creation),
        # not the bot token. Admins are re-added so the repo shows on
        # their dashboard.
        try:
            owners_team_id = await asyncio.to_thread(
                client.get_owners_team_id, self.org_name,
            )
        except ForgejoError as e:
            logger.warning("[git-mirror] Could not resolve Owners team: {}", e)
            return False

        # Bot first so it can write; admins after so they see it.
        for member in (BOT_USERNAME, *self.admin_usernames):
            try:
                await asyncio.to_thread(client.add_team_member, owners_team_id, member)
            except ForgejoError as e:
                logger.warning("[git-mirror] Could not add {} to Owners: {}", member, e)

        # The repo itself, its description, README, and the seed files
        # (documents/, ontology.toml, facts.toml) are all created and
        # owned by the memory stacklet. The archivist only writes Markdown
        # under the shared bucket into the already-provisioned repo.

        self._setup_done = True
        logger.info("[git-mirror] Setup complete: {}/{}", self.org_name, REPO_NAME)
        return True

    def _load_or_create_creds(self) -> MirrorCreds:
        """Read creds from disk, or mint a new password and persist it."""
        if self.creds_path.exists():
            try:
                data = json.loads(self.creds_path.read_text())
                return MirrorCreds(password=data["password"], token=data.get("token", ""))
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("[git-mirror] Bad creds file ({}), regenerating", e)

        creds = MirrorCreds(password=secrets.token_urlsafe(24), token="")
        self._save_creds(creds)
        return creds

    def _save_creds(self, creds: MirrorCreds | None = None) -> None:
        creds = creds or self._creds
        self.creds_path.parent.mkdir(parents=True, exist_ok=True)
        self.creds_path.write_text(json.dumps({
            "password": creds.password,
            "token": creds.token,
        }))
        try:
            os.chmod(self.creds_path, 0o600)
        except OSError:
            pass

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

    async def _lookup_path(self, client: ForgejoClient, paperless_id: int) -> str | None:
        """Find the current filepath for a Paperless doc, if any.

        Fast path: cache hit + verified by HEAD. Cold path: walk the repo
        tree and match the `-p<id>.md` filename suffix.
        """
        self._load_cache()
        cached = self._cache.get(paperless_id)
        if cached:
            existing = await asyncio.to_thread(
                client.get_file, self.repo_owner, REPO_NAME, cached,
            )
            if existing:
                return cached
            self._cache.pop(paperless_id, None)

        suffix_variants = (f"-p{paperless_id}.md", f"/p{paperless_id}.md")
        tree = await asyncio.to_thread(client.list_tree, self.repo_owner, REPO_NAME)
        for entry in tree:
            path = entry.get("path", "")
            if entry.get("type") == "blob" and any(path.endswith(s) or path == s.lstrip("/") for s in suffix_variants):
                self._cache[paperless_id] = path
                self._save_cache()
                return path
        return None

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
    ) -> dict:
        """Document frontmatter. Delegates to ``vault_entry.document_frontmatter``,
        injecting this mirror's known Paperless server version."""
        return document_frontmatter(
            title=title, date=date,
            correspondent=correspondent, document_type=document_type,
            category=category, persons=persons, tags=tags,
            paperless_id=paperless_id, paperless_url=paperless_url,
            processing=processing, model=model,
            paperless_version=self.paperless_version,
        )

    def _render(
        self,
        *,
        frontmatter: dict,
        body: str,
        correspondent: str | None,
        persons: list[str],
        summary: str | None = None,
        facts: list | None = None,
        action_items: list | None = None,
        source_link: tuple[str, str] | None = None,
    ) -> str:
        """Document mirror markdown. Delegates to ``vault_entry.render_document``."""
        return render_document(
            frontmatter=frontmatter, body=body,
            correspondent=correspondent, persons=persons,
            summary=summary, facts=facts, action_items=action_items,
            source_link=source_link,
        )

    def _render_capture(
        self, *,
        frontmatter: dict,
        body: str,
        kind: str,
        captured_at: str | None,
        source_uri: str | None,
        persons: list[str],
        summary: str | None = None,
        facts: list | None = None,
    ) -> str:
        """Capture mirror markdown. Delegates to ``vault_entry.render_capture``."""
        return render_capture(
            frontmatter=frontmatter, body=body, kind=kind,
            captured_at=captured_at, source_uri=source_uri, persons=persons,
            summary=summary, facts=facts,
        )

    def _commit_message(
        self,
        *,
        verb: str, title: str,
        paperless_id: int,
        processing: str, model: str | None,
        summary: str | None = None,
    ) -> str:
        """Build a commit with trailers. verb = 'learn' | 'update'.

        When a classifier summary is available it rides in the commit body
        between the subject and the trailers — turns `git log` on the
        mirror into a browsable archive log, and gives `git log --grep`
        a searchable index without a separate tool.
        """
        lines = [f"{verb}: {title}", ""]
        if summary:
            lines.append(summary.strip())
            lines.append("")
        lines.append(f"Paperless-Id: {paperless_id}")
        lines.append(f"Processing: {processing}")
        if model:
            lines.append(f"Model: {model}")
        return "\n".join(lines)

    # ── Publish ──────────────────────────────────────────────────────────

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
        if not await self.ensure_setup():
            return False

        client = ForgejoClient(url=self.code_url, token=self._creds.token)

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

        persons_raw = classification.get("persons") or classification.get("person") or []
        if isinstance(persons_raw, str):
            persons_raw = [persons_raw]
        persons = [p for p in persons_raw if isinstance(p, str) and p]

        target_path = self._filepath(date, paperless_id, title, bool(resolved_title))

        existing_path = await self._lookup_path(client, paperless_id)
        existing = None
        if existing_path:
            existing = await asyncio.to_thread(
                client.get_file, self.repo_owner, REPO_NAME, existing_path,
            )

        fm = self._frontmatter(
            title=title, date=date,
            correspondent=correspondent, document_type=document_type,
            category=category, persons=persons, tags=tags or [],
            paperless_id=paperless_id, paperless_url=paperless_url,
            processing=processing, model=model,
        )

        # The briefing's "Show Document" link wants the per-document
        # details page, not the instance root the caller hands us — the
        # same URL stamped into the frontmatter `resource` field.
        paperless_doc_url = document_resource_url(paperless_url, paperless_id)

        # Briefing block inputs come straight from the classifier. The
        # `summary` parameter on `publish()` is the multi-section
        # Markdown that goes into Paperless and the commit body — it
        # already contains its own `## Summary` / `## Facts` headings,
        # so feeding it into `_briefing_block` (which would wrap it in
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
            correspondent=correspondent, persons=persons,
            summary=briefing_summary,
            facts=briefing_facts,
            action_items=briefing_actions,
            source_link=source_link,
        )

        verb = "update" if existing else "learn"
        message = self._commit_message(
            verb=verb, title=title, paperless_id=paperless_id,
            processing=processing, model=model, summary=summary,
        )

        try:
            if existing and existing_path != target_path:
                await asyncio.to_thread(
                    client.delete_file,
                    self.repo_owner, REPO_NAME, existing_path,
                    sha=existing["sha"],
                    message=f"rename: {existing_path} → {target_path}\n\nPaperless-Id: {paperless_id}",
                )
                await asyncio.to_thread(
                    client.put_file,
                    self.repo_owner, REPO_NAME, target_path,
                    content=content, message=message,
                    author_name=BOT_USERNAME, author_email=BOT_EMAIL,
                )
            elif existing:
                await asyncio.to_thread(
                    client.put_file,
                    self.repo_owner, REPO_NAME, target_path,
                    content=content, message=message, sha=existing["sha"],
                    author_name=BOT_USERNAME, author_email=BOT_EMAIL,
                )
            else:
                await asyncio.to_thread(
                    client.put_file,
                    self.repo_owner, REPO_NAME, target_path,
                    content=content, message=message,
                    author_name=BOT_USERNAME, author_email=BOT_EMAIL,
                )
        except ForgejoError as e:
            logger.warning("[git-mirror] Publish failed for paperless #{}: {}", paperless_id, e)
            return False

        self._cache[paperless_id] = target_path
        self._save_cache()
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
        injecting this mirror's capture-hash length."""
        return capture_filepath(
            entity, kind, captured_at, title, hash_key, self._CAPTURE_HASH_LEN,
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
    ) -> dict:
        """Capture frontmatter. Delegates to ``vault_entry.capture_frontmatter``."""
        return capture_frontmatter(
            title=title, captured_at=captured_at, kind=kind,
            source_uri=source_uri, persons=persons, tags=tags,
            model=model, capture_id=capture_id,
        )

    async def read_capture(self, path: str) -> str | None:
        """Return the raw markdown of a capture entry, or None if missing.

        Used by ``CapturePipeline.reprocess`` to re-classify an
        already-filed capture without re-fetching the original binary.
        Best-effort: Forgejo errors are logged and surfaced as None
        so the caller can render a friendly "couldn't find it" reply.
        """
        if not await self.ensure_setup():
            return None
        client = ForgejoClient(url=self.code_url, token=self._creds.token)
        try:
            data = await asyncio.to_thread(
                client.get_file, self.repo_owner, REPO_NAME, path,
            )
        except ForgejoError as e:
            logger.warning("[git-mirror] read_capture {} failed: {}", path, e)
            return None
        if data is None:
            return None
        return data.get("content") or ""

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
    ) -> str | None:
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

        ``existing_path`` is the reprocess hook: when supplied, the
        write deletes the old file if the new title-derived path
        differs (a rename) and re-uses the same identity otherwise.
        The doc-mirror's `publish` already does this for renamed
        Paperless docs; captures pick up the same shape.

        Returns the path where the capture landed (relative to the
        repo root) on success, None when Forgejo is unreachable or
        the write failed. Failures are logged but never raised —
        captures are best-effort.
        """
        if not await self.ensure_setup():
            return None

        client = ForgejoClient(url=self.code_url, token=self._creds.token)

        resolved_title = classification.get("title") or title_hint
        title = resolved_title or "Capture"

        persons_raw = classification.get("persons") or classification.get("person") or []
        if isinstance(persons_raw, str):
            persons_raw = [persons_raw]
        persons = [p for p in persons_raw if isinstance(p, str) and p]

        hash_key = source_uri or body_text
        target_path = self._capture_filepath(
            entity=entity,
            kind=kind,
            captured_at=captured_at,
            title=title if resolved_title else None,
            hash_key=hash_key,
        )

        # Reprocess path: read the previous file at its old path. When
        # the title changes the slug changes too, so we'll delete the
        # old entry after writing the new one (same as the doc
        # mirror's rename handling).
        existing = None
        if existing_path and existing_path != target_path:
            existing = await asyncio.to_thread(
                client.get_file, self.repo_owner, REPO_NAME, existing_path,
            )
            existing_at_new = await asyncio.to_thread(
                client.get_file, self.repo_owner, REPO_NAME, target_path,
            )
        else:
            lookup_path = existing_path or target_path
            existing = await asyncio.to_thread(
                client.get_file, self.repo_owner, REPO_NAME, lookup_path,
            )
            existing_at_new = existing if lookup_path == target_path else None

        fm = self._capture_frontmatter(
            title=title,
            captured_at=captured_at,
            kind=kind,
            source_uri=source_uri,
            persons=persons,
            tags=tags or [],
            model=model,
            capture_id=capture_id,
        )

        briefing_summary = classification.get("summary")
        briefing_facts = classification.get("facts") or []

        content = self._render_capture(
            frontmatter=fm,
            body=body_text,
            kind=kind,
            captured_at=captured_at,
            source_uri=source_uri,
            persons=persons,
            summary=briefing_summary,
            facts=briefing_facts,
        )

        verb = "update" if existing else "capture"
        message_lines = [f"{verb}: {title}", ""]
        if briefing_summary:
            message_lines.append(briefing_summary.strip())
            message_lines.append("")
        if source_uri:
            message_lines.append(f"Source: {source_uri}")
        else:
            message_lines.append("Source: (paste)")
        if model:
            message_lines.append(f"Model: {model}")
        message = "\n".join(message_lines)

        try:
            await asyncio.to_thread(
                client.put_file,
                self.repo_owner, REPO_NAME, target_path,
                content=content, message=message,
                sha=existing_at_new["sha"] if existing_at_new else None,
                author_name=BOT_USERNAME, author_email=BOT_EMAIL,
            )
            # Title rename on reprocess: remove the prior file after
            # the new one is in place so the vault never has both.
            if existing_path and existing_path != target_path and existing:
                await asyncio.to_thread(
                    client.delete_file,
                    self.repo_owner, REPO_NAME, existing_path,
                    sha=existing["sha"],
                    message=f"rename: {existing_path} → {target_path}",
                )
        except ForgejoError as e:
            logger.warning(
                "[git-mirror] Capture publish failed for {}: {}",
                source_uri or "(paste)", e,
            )
            return None

        logger.info("[git-mirror] {} → {}", verb, target_path)
        return target_path
