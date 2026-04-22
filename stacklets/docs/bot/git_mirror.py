"""Git mirror — publishes classified Paperless documents to Forgejo.

One file per Paperless document at `YYYY/MM/YYYY-MM-DD-<slug>.md`.
Filename uses a title slug when AI classification produced one, falls
back to `paperless-<id>` otherwise. The filename is stable after the
first AI pass — a later reprocess updates content but doesn't chase
title tweaks across the URL space.

The body is the best representation we have:
  - AI available → LLM-cleaned markdown from `_reformat`
  - AI unavailable → raw Paperless OCR text

Metadata rides in YAML frontmatter (Obsidian/Dataview compatible) plus
a commit trailer `Paperless-Id: N` that enables git-native lookups.

Delete handling is deferred to a future `stack docs reconcile` job —
v1 leaves deleted Paperless docs as stale markdown in git history.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from loguru import logger

from stack.forgejo import ForgejoClient, ForgejoError


REPO_NAME = "documents"
REPO_DESCRIPTION = (
    "Read-only mirror of Paperless — written by archivist-bot. "
    "Paperless is the database; edits here get overwritten on reprocess."
)
BOT_USERNAME = "archivist-bot"
BOT_EMAIL = "archivist-bot@local"
TOKEN_NAME = "archivist-git-mirror"
TOKEN_SCOPES = ["write:repository", "read:repository", "read:user", "write:organization"]
ORG_DESCRIPTION = "Your family's Forgejo — documents, knowledge, and shared repos."


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
        """Ensure archivist-bot user, token, repo, and collaborators exist.

        Returns True if setup succeeded (or was already done), False if
        Forgejo is unreachable. Subsequent calls short-circuit once
        `_setup_done` is set.
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

        client.token = self._creds.token

        # ── Org + team membership ────────────────────────────────────
        # Orgs are the right home for family-wide repos (documents now,
        # brain/calendar later). Admins see every org repo on their
        # dashboard without per-repo watches.
        try:
            await asyncio.to_thread(
                client.create_org, self.org_name, ORG_DESCRIPTION,
            )
        except ForgejoError as e:
            logger.warning("[git-mirror] Could not ensure org {}: {}", self.org_name, e)
            return False

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

        try:
            await asyncio.to_thread(
                client.create_repo, self.org_name, REPO_NAME,
                description=REPO_DESCRIPTION, private=True, owner_is_org=True,
            )
        except ForgejoError as e:
            logger.warning("[git-mirror] Could not ensure repo: {}", e)
            return False

        # `create_repo` is idempotent on existing repos, so the description
        # sync is a separate step for deployments that already have the
        # repo with an older description. Admin-basic auth because the
        # bot's token isn't scoped to repo settings.
        admin_client = ForgejoClient(
            url=self.code_url,
            admin_user=self.admin_user, admin_password=self.admin_password,
        )
        try:
            await asyncio.to_thread(
                admin_client.update_repo, self.org_name, REPO_NAME,
                description=REPO_DESCRIPTION,
            )
        except ForgejoError as e:
            logger.warning("[git-mirror] Could not sync repo description: {}", e)

        await self._ensure_readme(client)

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

    async def _ensure_readme(self, client: ForgejoClient) -> None:
        """Sync the README to match the current `_render_readme()` output.

        Compares the existing README byte-for-byte against the current
        render. Writes only when they differ — keeps the README in lockstep
        with the code, so wording changes reach live instances on the
        next archivist boot without anyone having to delete the old file.
        """
        import base64

        desired = self._render_readme()
        existing = await asyncio.to_thread(
            client.get_file, self.repo_owner, REPO_NAME, "README.md",
        )
        sha = None
        verb = "seed"
        if existing:
            sha = existing.get("sha")
            try:
                body = base64.b64decode(existing.get("content", "")).decode()
                if body == desired:
                    return
                verb = "refresh"
            except Exception:
                # Undecodable or missing content — fall through and rewrite.
                verb = "refresh"

        await asyncio.to_thread(
            client.put_file,
            self.repo_owner, REPO_NAME, "README.md",
            content=desired,
            message=f"chore: {verb} README",
            sha=sha,
            author_name=BOT_USERNAME, author_email=BOT_EMAIL,
        )

    def _render_readme(self) -> str:
        return (
            "# Documents Archive\n\n"
            "> **Read-only mirror.** This repo is written by `archivist-bot`.\n"
            "> Manual edits survive only until the next reprocess — the bot\n"
            "> rewrites the frontmatter and body whenever Paperless's doc\n"
            "> changes or classification re-runs. To keep a change, edit the\n"
            "> source in Paperless; the mirror will follow.\n\n"
            "Auto-generated by **archivist-bot**. One file per document filed\n"
            "via the docs stacklet's `#documents` Matrix room. Paperless-ngx\n"
            "stays the canonical database — this repo is the human-browsable\n"
            "markdown mirror.\n\n"
            "## Layout\n\n"
            "    YYYY/MM/YYYY-MM-DD-<slug>-p<id>.md     one doc per file\n"
            "    _unfiled/<slug>-p<id>.md               no classified date\n\n"
            "The `-p<id>` suffix carries the Paperless document id. Makes\n"
            "idempotency survive cache loss: the archivist can re-build its\n"
            "`paperless_id → path` cache by walking the tree.\n\n"
            "## Frontmatter\n\n"
            "YAML, Obsidian- and Dataview-compatible:\n\n"
            "    ---\n"
            "    title: ADAC Rechnung März 2026\n"
            "    date: 2026-03-15\n"
            "    correspondent: ADAC\n"
            "    document_type: Invoice\n"
            "    category: Insurance\n"
            "    persons: [Homer]\n"
            "    tags: [Insurance, \"Person: Homer\"]\n"
            "    paperless_id: 247\n"
            "    paperless_url: http://docs.home/documents/247\n"
            "    processing: ai_formatted\n"
            "    model: qwen3.5:14b\n"
            "    source: paperless\n"
            "    added: 2026-04-19T10:15:00Z\n"
            "    ---\n\n"
            "### `processing` values\n\n"
            "Describes where the **body** came from, independent of whether\n"
            "AI classification ran:\n\n"
            "- `ai_formatted` — LLM rewrote Paperless's OCR into clean\n"
            "  markdown. `model` records the LLM used.\n"
            "- `ocr` — Paperless's OCR output, unchanged. Used when the AI\n"
            "  stacklet isn't up or the reformat step failed.\n"
            "- `original` — original bytes of a text-like file (`.md`,\n"
            "  `.json`, `.yaml`, ...). No transformation applied; round-trips\n"
            "  byte-for-byte.\n\n"
            "Classification (`topics`, `persons`, `correspondent`,\n"
            "`document_type`) reflects what the LLM decided and is\n"
            "orthogonal to `processing`.\n\n"
            "## Commits\n\n"
            "    learn: <title>         new document\n"
            "    update: <title>        reprocessed existing document\n"
            "    rename: <old> → <new>  title-driven filename change\n\n"
            "When the classifier produced a summary it rides in the commit\n"
            "body as `## Summary` / `## Facts` / `## Parties` sections, so\n"
            "`git log` reads like a narrated archive log and `git log --grep`\n"
            "searches across summaries out of the box. Each commit message\n"
            "carries a `Paperless-Id: <N>` trailer.\n\n"
            "## Deletions\n\n"
            "Documents deleted in Paperless stay in this repo until a future\n"
            "`stack docs reconcile` pass tombstones them. Until then, the\n"
            "`paperless_url` in frontmatter will 404 when clicked through.\n\n"
            "## Editing\n\n"
            "Meant to be append-only from the bot's side. Manual edits\n"
            "survive reprocesses only if they don't touch fields the\n"
            "archivist rewrites (title, frontmatter, body). Use Forgejo's\n"
            "web UI or clone the repo as an Obsidian vault.\n"
        )

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
        """Filesystem-safe slug: ASCII-ish, lowercase, hyphen-separated."""
        normalized = unicodedata.normalize("NFKD", text)
        ascii_ = normalized.encode("ascii", "ignore").decode()
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_).strip("-").lower()
        return slug[:60] or "document"

    def _filepath(self, date: str | None, paperless_id: int, title: str | None, has_title: bool) -> str:
        """Build YYYY/MM/YYYY-MM-DD-<slug>-p<id>.md.

        `has_title` is True when we have a slug-worthy title (from AI
        classification or the caller's fallback filename) — as opposed
        to the generic `Paperless #N`. The `-p<id>` suffix always appears
        so the Paperless ID is recoverable from the filename alone,
        surviving cache loss without needing to scan frontmatter.
        """
        if date and re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            y, m, _ = date.split("-")
            prefix = f"{y}/{m}/{date}"
        else:
            prefix = "_unfiled"

        if has_title and title:
            slug = self._slug(title)
            return f"{prefix}-{slug}-p{paperless_id}.md" if prefix != "_unfiled" else f"_unfiled/{slug}-p{paperless_id}.md"
        return f"{prefix}-p{paperless_id}.md" if prefix != "_unfiled" else f"_unfiled/p{paperless_id}.md"

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
        """Assemble the frontmatter dict in a stable key order."""
        import datetime as dt
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        fm: dict = {"title": title}
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
        fm["processing"] = processing
        if model:
            fm["model"] = model
        if self.paperless_version:
            fm["paperless_version"] = self.paperless_version
        fm["source"] = "paperless"
        fm["added"] = now
        return fm

    def _render(
        self,
        *,
        frontmatter: dict,
        body: str,
        correspondent: str | None,
        persons: list[str],
        wiki_header: bool = True,
    ) -> str:
        """Assemble frontmatter + optional wiki-link header + body."""
        fm_yaml = yaml.safe_dump(
            frontmatter, sort_keys=False, allow_unicode=True, default_flow_style=False,
        ).strip()
        parts = ["---", fm_yaml, "---", ""]

        parts.append(f"# {frontmatter.get('title', 'Untitled')}")
        parts.append("")

        if wiki_header and (correspondent or persons):
            bits = []
            if correspondent:
                bits.append(f"**From:** [[{correspondent}]]")
            if persons:
                bits.append("**About:** " + ", ".join(f"[[{p}]]" for p in persons))
            parts.append("> " + " · ".join(bits))
            parts.append("")

        parts.append(body.strip())
        parts.append("")
        return "\n".join(parts)

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
        content = self._render(
            frontmatter=fm, body=body_text,
            correspondent=correspondent, persons=persons,
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
