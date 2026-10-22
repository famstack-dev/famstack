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
import re
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from loguru import logger

from stack.forgejo import FileChange, ForgejoClient, ForgejoError
from stack.card_links import About, correspondent_card, topic_card, without_person_tags
from stack.cards import card_id_from
from stack.frontmatter import FrontmatterError
from stack.frontmatter import parse as frontmatter_parse
from stack.tags import card_tags

from diary_room import household_now

if TYPE_CHECKING:
    from collections.abc import Sequence

    from vault_context import VaultContext
# Sibling stacklets resolve through `stacklets/` on sys.path: in the
# bot-runner container that directory is the read-only mount, locally it
# is the source tree. The path goes on here, before the import that needs
# it, rather than relying on who loads whom (as in `pipeline.py`).
_STACKLETS_DIR = Path(__file__).resolve().parents[2]
if str(_STACKLETS_DIR) not in sys.path:
    sys.path.insert(0, str(_STACKLETS_DIR))
# A note's card and sidecar are the memory stacklet's format (ADR-018).
from memory.card_history import CardHistory, history_of, versions_from_forgejo  # noqa: E402
from memory.field_ops import FieldOp, FieldValues, apply_ops  # noqa: E402
from memory.note_card import Source, card_fields, sidecar_path, sidecar_text  # noqa: E402
# Who a commit to the memory repo is by is the memory stacklet's rule.
from memory.lib import NoServerName, commit_author  # noqa: E402
from vault_entry import (  # noqa: E402
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
from stack.lists import (  # noqa: E402
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
from stack.people import Person, resolve  # noqa: E402
from stack.vault import topic_of  # noqa: E402


# The shared family knowledge vault. The memory stacklet creates and
# seeds the org + repo (description, README, ontology, facts); the
# archivist only writes Markdown under entity-rooted paths (see module
# docstring), and skips mirroring entirely if memory hasn't provisioned
# the repo yet.
REPO_NAME = "memory"
# How many commits of a card a refile reads: one page of Forgejo's commits
# API. A note is filed, corrected and edited a handful of times.
_HISTORY_COMMITS = 50

BOT_USERNAME = "archivist-bot"
BOT_EMAIL = "archivist-bot@local"


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


def _validate_on_write(frontmatter: dict) -> None:
    """Log (never block) if a filing's frontmatter violates the vault format.

    Never raises: losing a family's document to a schema violation is
    worse than the violation. If a builder produces invalid frontmatter
    (against docs/design/brain/vault-format.md) we log it and file the
    document anyway — the log is the breadcrumb, a reconcile heals the
    metadata. Builder bugs are caught deterministically by the builder
    tests, not by blocking a live filing.
    """
    from stack.frontmatter import validate as frontmatter_validate

    errors = frontmatter_validate(frontmatter)
    if errors:
        logger.warning(
            "[git-mirror] frontmatter violates vault-format schema, filing "
            "anyway: {}", "; ".join(errors),
        )


def code_commit() -> str:
    """The famstack commit this process runs, as `stack up` stamped it.

    `STACK_COMMIT` is rendered into every stacklet's env, the bot runner's
    included; a process started some other way says `unknown`.
    """
    return os.environ.get("STACK_COMMIT", "").strip() or "unknown"


def _commit_author(submitter: str | None) -> tuple[str, str]:
    """`(author_name, author_email)` for a capture commit.

    The family member who filed it, by the memory's rule (`commit_author`),
    with the Matrix server's name the bot runner's environment carries, so
    the archivist's commits and every other path's name them alike. The
    bot when nobody did. Raises `NoServerName` when the environment has none.
    """
    if not submitter:
        return BOT_USERNAME, BOT_EMAIL
    return commit_author(submitter, os.environ.get("MATRIX_SERVER_NAME", ""))


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
TOKEN_NAME = "archivist-git-mirror"
TOKEN_SCOPES = ["write:repository", "read:repository", "read:user"]


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
    # A note's sidecar (`memory.note_card`), "" for every other capture.
    sidecar: str = ""
    # The field edits that no longer fit the card, each with why (`memory.field_ops`).
    left_out: tuple[str, ...] = ()


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
    # "family" matches famstack's stock layout; non-family deployments
    # override via stack.toml [core] shared_bucket → env var
    # SHARED_BUCKET → archivist → here.
    shared_bucket: str = "family"
    paperless_version: str = ""
    # The family's people and calendar, read from the local copy of the
    # family memory; without it, dates go on a calendar started here and
    # name nobody.
    vault: VaultContext | None = None

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

    async def vault_client(self) -> ForgejoClient | None:
        """A client for the vault repo, or None while Forgejo is not set up."""
        if not await self.ensure_setup():
            return None
        return ForgejoClient(url=self.code_url, token=self._setup_creds.token)

    @property
    def _setup_creds(self) -> MirrorCreds:
        """The credentials `ensure_setup()` loaded.

        Every caller runs after `ensure_setup()` returned True, which is
        what sets them. Reaching this without it is a bug, and it says so
        here instead of failing later on a None.
        """
        if self._creds is None:
            raise RuntimeError("GitMirror used before ensure_setup() succeeded")
        return self._creds

    def _save_creds(self, creds: MirrorCreds | None = None) -> None:
        creds = creds or self._setup_creds
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

    async def _lookup_capture_path(
        self, client: ForgejoClient, target_path: str,
    ) -> str | None:
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
        prefix = f"{parts[0]}/{parts[1]}/"              # <entity>/<kind>s/
        suffix = "-" + target_path.rsplit("-", 1)[-1]   # -<hash>.md
        try:
            tree = await asyncio.to_thread(
                client.list_tree, self.repo_owner, REPO_NAME,
            )
        except ForgejoError:
            return None
        match = None
        for entry in tree:
            path = entry.get("path", "")
            if entry.get("type") != "blob":
                continue
            if path.startswith(prefix) and path.endswith(suffix):
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
        _validate_on_write(fm)
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
        client: ForgejoClient,
        *,
        topic: str,
        topic_name: str = "",
        record_path: str,
        record_title: str,
        action_items: list | None,
        message: str,
        author_name: str,
        author_email: str,
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
            client, path=path,
            start=new_list(title, path=path, topics=[topic], description=topic_list_description(
                title, os.environ.get("LANGUAGE", ""))),
            items=items, record_path=record_path, previous_path=previous_path,
            message=message, author_name=author_name, author_email=author_email,
        )

    async def _put_on_list(
        self,
        client: ForgejoClient,
        *,
        path: str,
        start: str,
        items: list[Item],
        record_path: str,
        previous_path: str,
        message: str,
        author_name: str,
        author_email: str,
    ) -> bool:
        """Add a record's `items` to the list at `path`, started as `start` if missing.

        A record's items go on the list once: when the list already has
        items from it, a refile adds none, because the model words them
        differently each time and the family may have ticked, reworded or
        removed them since. When a refile renamed the record
        (`previous_path`), those items are linked to its new path instead.
        An item already on the list is left alone: the same text, and on a
        calendar the same date too, since the school's Elternabend comes
        round again.
        """
        try:
            existing = await asyncio.to_thread(
                client.get_file, self.repo_owner, REPO_NAME, path,
            )
            prior = (existing or {}).get("content") or ""
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
                client.put_file,
                self.repo_owner, REPO_NAME, path,
                content=content, message=message,
                sha=existing["sha"] if existing else None,
                author_name=author_name, author_email=author_email,
            )
            return True
        except ListError as e:
            logger.warning("[git-mirror] {} is not a list, items not added: {}", path, e)
            return False
        except ForgejoError as e:
            logger.warning("[git-mirror] Items not added to {}: {}", path, e)
            return False

    async def _fold_document_todos(
        self,
        client: ForgejoClient,
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
                client,
                topic=self._slug(topic),
                topic_name=topic.strip(),
                record_path=record_path,
                record_title=record_title,
                previous_path=previous_path,
                action_items=classification.get("action_items") or [],
                message=f"chore(lists): archivist added action items to {self._slug(topic)}",
                author_name=BOT_USERNAME,
                author_email=BOT_EMAIL,
                today=today,
            )

    async def _fold_document_dates(
        self,
        client: ForgejoClient,
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
            client, path=path,
            start=new_list(title, path=path, description=calendar_description(language),
                           kind=CALENDAR),
            items=entries, record_path=record_path, previous_path=previous_path,
            message="chore(lists): archivist added dates to the calendar",
            author_name=BOT_USERNAME, author_email=BOT_EMAIL,
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
        client: ForgejoClient,
        *,
        target_path: str,
        classification: dict,
        author_name: str,
        author_email: str,
        previous_path: str = "",
        today: str = "",
    ) -> None:
        topic = self._topic_of_capture(target_path)
        await self._fold_action_items_into_list(
            client,
            topic=topic,
            record_path=target_path,
            previous_path=previous_path,
            record_title=str(classification.get("title") or "").strip()
            or Path(target_path).stem,
            action_items=classification.get("action_items") or [],
            message=f"chore(lists): {author_name} added action items to {topic}",
            author_name=author_name,
            author_email=author_email,
            today=today,
        )

    # ── Publish ──────────────────────────────────────────────────────────

    # ── The cards a record links ─────────────────────────────────────────
    #
    # A record links the cards of the people, the correspondent and the
    # topic it names (ADR-017 §3, §10). People are declared by the family;
    # a correspondent's card and a topic's are written here the first time
    # a record names them, and never rewritten: from then on they are the
    # family's (`stack.card_links`).

    def _people(self) -> list[Person]:
        """The declared people names resolve through; none without a local copy."""
        return (self.vault.people() if self.vault else None) or []

    def _tags(self, tags: list[str] | None) -> list[str]:
        """The tags a record is written with: topics by the ontology's id, the
        rest as keywords, no `Person:` tags (ADR-017 §4, `stack.tags`)."""
        if self.vault:
            ontology = self.vault.ontology()
        else:
            from memory.lib import get_ontology
            ontology = get_ontology()
        return card_tags(without_person_tags(tags or []), ontology)

    async def _declare(self, client: ForgejoClient, path: str, text: str) -> None:
        """Write the card at `path` when the memory repo has none there yet.

        Created, never updated: a card that appeared meanwhile makes
        Forgejo refuse the write, and the card that is there stays.
        """
        try:
            if await asyncio.to_thread(client.get_file, self.repo_owner, REPO_NAME, path):
                return
            await asyncio.to_thread(
                client.put_file, self.repo_owner, REPO_NAME, path,
                content=text, message=f"declare: {path}",
                author_name=BOT_USERNAME, author_email=BOT_EMAIL,
            )
        except ForgejoError as e:
            logger.warning("[git-mirror] could not declare {}: {}", path, e)
            return
        logger.info("[git-mirror] declared {}", path)

    async def _declare_topic(self, client: ForgejoClient, record_path: str) -> None:
        """Give the topic a record was filed into its card, the first time."""
        if found := topic_of(record_path):
            await self._declare(client, *topic_card(*found))

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

        client = ForgejoClient(url=self.code_url, token=self._setup_creds.token)

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

        existing_path = await self._lookup_path(client, paperless_id)
        existing = None
        if existing_path:
            existing = await asyncio.to_thread(
                client.get_file, self.repo_owner, REPO_NAME, existing_path,
            )

        fm = self._frontmatter(
            title=title, date=date,
            correspondent=correspondent, document_type=document_type,
            category=category, persons=about.ids, tags=self._tags(tags),
            paperless_id=paperless_id, paperless_url=paperless_url,
            processing=processing, model=model,
            identity=identity_for((existing or {}).get("content"), "document", target_path),
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
        _report_unknown(target_path, about)
        if correspondent:
            await self._declare(client, *correspondent_card(
                correspondent, _strings(classification.get("correspondent_aliases")),
                bucket=self.shared_bucket))
        await self._fold_document_todos(client, classification=classification,
                                        record_path=target_path, record_title=title,
                                        previous_path=existing_path or "")
        await self._fold_document_dates(client, classification=classification,
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
        goes into its sidecar (ADR-018 §3, `memory.note_card`).
        """
        fm = capture_frontmatter(
            title=title, captured_at=captured_at, kind=kind,
            source_uri=source_uri, persons=persons, tags=tags,
            model=model, capture_id=capture_id, filed_by=filed_by,
            identity=identity,
        )
        if kind == "note":
            fm = card_fields(fm)
        _validate_on_write(fm)
        return fm

    async def card_history(self, path: str, card_id: str) -> CardHistory | None:
        """What a refile of the note at `path` applies again of its history
        (ADR-018 §7), read through Forgejo: the commits of its path and its
        card and sidecar at each. None when Forgejo cannot be read.
        """
        if not await self.ensure_setup():
            return None
        client = ForgejoClient(url=self.code_url, token=self._setup_creds.token)

        def at(sha: str, file: str) -> str:
            found = client.get_file(self.repo_owner, REPO_NAME, file, ref=sha)
            return str((found or {}).get("content") or "")

        def versions():
            commits = client.list_commits(self.repo_owner, REPO_NAME, path=path,
                                          limit=_HISTORY_COMMITS)
            return versions_from_forgejo(commits, at, path=path, card_id=card_id)

        try:
            return history_of(await asyncio.to_thread(versions))
        except ForgejoError as e:
            logger.warning("[git-mirror] history of {} not read: {}", path, e)
            return None

    def _field_values(self, people: list[Person]) -> FieldValues:
        """What a field edit may name here: the declared people, the ontology."""
        if self.vault:
            ontology = self.vault.ontology()
        else:
            from memory.lib import get_ontology
            ontology = get_ontology(None)
        return FieldValues(person_ids=frozenset(p.id for p in people), ontology=ontology)

    async def read_capture(self, path: str) -> str | None:
        """Return the raw markdown of a capture entry, or None if missing.

        Used by ``CapturePipeline.reprocess`` to re-classify an
        already-filed capture without re-fetching the original binary.
        Best-effort: Forgejo errors are logged and surfaced as None
        so the caller can render a friendly "couldn't find it" reply.
        """
        if not await self.ensure_setup():
            return None
        client = ForgejoClient(url=self.code_url, token=self._setup_creds.token)
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
        root) and its card id on success, None when Forgejo is unreachable or
        the write failed. Failures are logged but never raised —
        captures are best-effort.
        """
        if not await self.ensure_setup():
            return None

        client = ForgejoClient(url=self.code_url, token=self._setup_creds.token)

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
        if existing_path is None:
            existing_path = await self._lookup_capture_path(client, target_path)

        # A capture keeps the path it was first filed under. Topic pages,
        # list items and the link in the chat point at it, so a new title
        # or a correction from someone else in the room changes what the
        # entry says, never where it is.
        if existing_path:
            target_path = existing_path
        existing = await asyncio.to_thread(
            client.get_file, self.repo_owner, REPO_NAME, target_path,
        )

        # Attribute the capture to whoever filed it: the localpart goes in
        # `filed_by` frontmatter, and the same identity becomes the git commit
        # author (committer stays the bot). Both come from `submitter`.
        try:
            author_name, author_email = _commit_author(submitter)
        except NoServerName as e:
            logger.error("[git-mirror] Capture not filed: {}", e)
            return None
        entry = self.capture_entry(
            target_path=target_path, existing=(existing or {}).get("content"),
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
            if kind == "note":
                await self._write_note(client, target_path, existing, content, entry.sidecar,
                                       message=message, author_name=author_name,
                                       author_email=author_email)
            else:
                await asyncio.to_thread(
                    client.put_file,
                    self.repo_owner, REPO_NAME, target_path,
                    content=content, message=message,
                    sha=existing["sha"] if existing else None,
                    author_name=author_name, author_email=author_email,
                )
        except ForgejoError as e:
            logger.warning(
                "[git-mirror] Capture publish failed for {}: {}",
                source_uri or "(paste)", e,
            )
            return None

        logger.info("[git-mirror] {} → {}", verb, target_path)
        _report_unknown(target_path, about)
        await self._declare_topic(client, target_path)
        await self._fold_capture_todos(
            client,
            target_path=target_path,
            classification=classification,
            author_name=author_name,
            author_email=author_email,
            previous_path=existing_path or "",
        )
        return Filed(target_path, str(fm.get("card_id") or ""))

    async def _write_note(self, client: ForgejoClient, path: str, existing: dict | None,
                          card: str, sidecar: str, *, message: str,
                          author_name: str, author_email: str) -> None:
        """A note's card and its sidecar, in one commit, each against the
        blob it was read at, so neither changes without the other."""
        side_path = sidecar_path(path)
        side = await asyncio.to_thread(client.get_file, self.repo_owner, REPO_NAME, side_path)
        changes = [
            FileChange("update", path, card, sha=existing["sha"]) if existing
            else FileChange("create", path, card),
            FileChange("update", side_path, sidecar, sha=side["sha"]) if side
            else FileChange("create", side_path, sidecar),
        ]
        await asyncio.to_thread(
            client.change_files, self.repo_owner, REPO_NAME, changes,
            message=message, author_name=author_name, author_email=author_email,
        )

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
    # this method is just the Forgejo read-fold-write around it.

    async def _find_email_thread(
        self, client: ForgejoClient, entity: str, digest: str,
    ) -> str | None:
        """The existing thread file for this digest, or None.

        Walks the entity's email tree for a ``…-<digest>.md`` blob. The
        digest is over the thread root, stable across the conversation, so
        this returns the file the first message created — replies append to
        it even when their title (and slug) has drifted.
        """
        suffix = f"-{digest}.md"
        prefix = f"{entity}/emails/"
        try:
            tree = await asyncio.to_thread(
                client.list_tree, self.repo_owner, REPO_NAME,
            )
        except ForgejoError:
            return None
        for entry in tree:
            path = entry.get("path", "")
            if (entry.get("type") == "blob"
                    and path.startswith(prefix)
                    and path.endswith(suffix)):
                return path
        return None

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
        already folded, an idempotent no-op), or None when Forgejo is
        unreachable or the write failed. Best-effort like the rest of the mirror.
        """
        if not await self.ensure_setup():
            return None

        client = ForgejoClient(url=self.code_url, token=self._setup_creds.token)

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
        target_path = await self._find_email_thread(client, entity, digest)
        if target_path is None:
            target_path = self._capture_filepath(
                entity=entity,
                kind="email",
                captured_at=captured_at,
                title=title if resolved_title else None,
                hash_key=thread_uri or body_text,
            )

        existing = await asyncio.to_thread(
            client.get_file, self.repo_owner, REPO_NAME, target_path,
        )
        existing_content = (existing.get("content") if existing else None) or None

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
        summary = classification.get("summary")
        message_lines = [f"{verb}: {title}", ""]
        if summary:
            message_lines.append(summary.strip())
            message_lines.append("")
        message_lines.append(f"Thread: {thread_uri or '(none)'}")
        if message_id:
            message_lines.append(f"Message-Id: {message_id}")
        if model:
            message_lines.append(f"Model: {model}")
        commit = "\n".join(message_lines)

        try:
            await asyncio.to_thread(
                client.put_file,
                self.repo_owner, REPO_NAME, target_path,
                content=content, message=commit,
                sha=existing["sha"] if existing else None,
                author_name=BOT_USERNAME, author_email=BOT_EMAIL,
            )
        except ForgejoError as e:
            logger.warning(
                "[git-mirror] Email fold failed for {}: {}",
                thread_uri or "(no thread)", e,
            )
            return None

        logger.info("[git-mirror] {} email → {}", verb, target_path)
        _report_unknown(target_path, about)
        await self._declare_topic(client, target_path)
        await self._fold_capture_todos(
            client,
            target_path=target_path,
            classification=classification,
            author_name=BOT_USERNAME,
            author_email=BOT_EMAIL,
        )
        return Filed(target_path, _card_id_in(content))
