"""Archivist — the document filing bot.

The killer feature of famstack: send a photo of a document to your family
chat, and it gets OCR'd, classified by AI, tagged, and filed in Paperless
— all automatically. No scanning app, no manual filing, no desktop needed.

The pipeline for every document:
  1. User sends a file/photo to the #documents Matrix room
  2. Archivist downloads it from Matrix
  3. Uploads to Paperless-ngx, which runs OCR and extracts text
  4. Sends the OCR text to the LLM (oMLX) for classification
  5. LLM returns structured JSON: title, category, person, type, correspondent
  6. Archivist creates any missing tags/types in Paperless and applies them
  7. Optionally reformats the raw OCR text into clean Markdown
  8. Reports back in the chat room with a summary and link

Beyond single files, the archivist also handles:
  - Multi-page scans: type ( → upload pages → type ) to combine into one PDF
  - URL archiving: paste a PDF or Google Docs link to download and file it
  - Document search: type any text to full-text search across all documents
  - Bilingual messages (en/de) loaded from messages/archivist.yml

Refactored from a standalone archivist_bot.py (1099 lines) into a
MicroBot subclass. The base class handles Matrix login, E2E encryption,
and the sync loop — this file focuses purely on document processing logic.
"""

import asyncio
import io
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import aiohttp
import yaml
from loguru import logger
from PIL import Image
from nio import (
    AsyncClient,
    RoomMessageMedia,
    RoomMessageImage,
    RoomMessageFile,
    RoomMessageText,
)

from capture_tags import CaptureTagCache
from extractors import TextExtractor, UrlExtractor
from git_mirror import GitMirror
from matching import build_document_event
from microbot import MicroBot
from capabilities import ModelCapabilities
from pipeline import (
    Classifier,
    DEFAULT_CLASSIFY_MAX_CHARS,
    PaperlessAPI,
    PaperlessDuplicateError,
    enrich_document,
)
from stack import resolve_model

# Make sibling stacklets importable. In the bot-runner container,
# `/stacklets/` is mounted read-only and holds all stacklets; locally
# it's the source tree. Either way, the path resolves the same way.
_STACKLETS_DIR = Path(__file__).resolve().parents[2]
if str(_STACKLETS_DIR) not in sys.path:
    sys.path.insert(0, str(_STACKLETS_DIR))
from memory.lib import (  # noqa: E402
    correspondents_prompt_section as _memory_correspondents_section,
    get_ontology as _get_memory_ontology,
    load_correspondents_from_vault as _load_memory_correspondents,
)
from room_context import normalize_alias  # noqa: E402
from text_utils import (  # noqa: E402
    clean_filename as _clean_filename,
    google_docs_export_url as _google_docs_export_url,
    is_just_url as _is_just_url,
    looks_like_paste,
    strip_reply_fallback as _strip_reply_fallback,
)
from reply_presenter import render_capture_reply, render_filing_reply  # noqa: E402
from document_pipeline import (  # noqa: E402
    DocumentPipeline,
    FilingOutcome,
    utc_now_isoformat,
)
from search_service import SearchService  # noqa: E402
from capture_pipeline import CapturePipeline, CaptureOutcome  # noqa: E402
from notifier import MatrixNotifier  # noqa: E402


@contextmanager
def _timed(operation: str):
    """Log an operation with its elapsed time. Use as a context manager:

        with _timed("LLM classify"):
            result = await llm_call(...)

    Logs start and completion with duration. On exception, logs the error
    with duration — useful for diagnosing timeouts and slow services.
    """
    t0 = time.monotonic()
    logger.info("[archivist] {} started", operation)
    try:
        yield
    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error("[archivist] {} failed after {:.1f}s: {}", operation, elapsed, e)
        raise
    else:
        elapsed = time.monotonic() - t0
        logger.info("[archivist] {} completed in {:.1f}s", operation, elapsed)

def _llm_error_for_chat(
    pipeline_error: tuple[str, str] | None,
    *, name: str, openai_url: str, link: str,
) -> tuple[str, dict] | None:
    """Map a pipeline llm_error tuple to (translation-key, format-kwargs).

    The pipeline speaks in transport terms ("unavailable", "model_missing",
    "timeout"); the chat reply needs translation keys that already know
    how to render the document's name and a link back to Paperless.
    Returns None when there was no error.
    """
    if not pipeline_error:
        return None
    kind, detail = pipeline_error
    if kind == "unavailable":
        return ("llm_unavailable", {"name": name, "url": openai_url, "link": link})
    if kind == "model_missing":
        return ("llm_model_missing", {"name": name, "model": detail, "link": link})
    if kind == "timeout":
        return ("llm_timeout", {"name": name, "link": link})
    return None


# ── Constants ────────────────────────────────────────────────────────────────

SUPPORTED_MSGTYPES = {"m.file", "m.image"}

SCAN_BEGIN = {"scan", "("}
SCAN_END = {"done", "fertig", ")"}

HELP_COMMANDS = {"help", "hilfe", "?"}


# ── Translations ─────────────────────────────────────────────────────────────

_messages_path = Path(__file__).parent / "messages" / "archivist.yml"
_MESSAGES = yaml.safe_load(_messages_path.read_text(encoding="utf-8"))


def _t(lang: str, key: str, **kwargs) -> str:
    """Get a translated message. Falls back to English if key is missing."""
    lang_msgs = _MESSAGES.get(lang, _MESSAGES["en"])
    text = lang_msgs.get(key, _MESSAGES["en"].get(key, key))
    if kwargs:
        text = text.format(**kwargs)
    return text.rstrip("\n")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _combine_images_to_pdf(files: list[tuple[str, bytes]]) -> bytes:
    """Combine multiple image files into a single multi-page PDF."""
    images = []
    for _, data in files:
        img = Image.open(io.BytesIO(data))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        images.append(img)

    pdf_buffer = io.BytesIO()
    images[0].save(pdf_buffer, "PDF", save_all=True, append_images=images[1:])
    return pdf_buffer.getvalue()



# ── ArchivistBot ─────────────────────────────────────────────────────────────

class ArchivistBot(MicroBot):
    """Document filing bot — watches a Matrix room for uploads, classifies
    them with an LLM, and files them in Paperless-ngx."""

    name = "archivist-bot"

    def __init__(self, homeserver, user_id, password, session_dir, **settings):
        super().__init__(homeserver, user_id, password, session_dir, **settings)
        # Shared config from env vars — rendered by the CLI from stack.toml
        # URL contract: `*_url` is container-internal (compose service
        # hostname like `stack-paperless:8000` / `stack-code:3000`) and
        # MUST only feed bot→service API calls (PaperlessAPI, GitMirror,
        # etc). `*_public_url` is the externally reachable URL and is
        # the ONLY URL allowed in user-surfacing output (chat messages,
        # committed memory-vault metadata, event envelopes). There is
        # no fallback from public to internal -- a docker hostname in a
        # chat link is worse than no link at all, since the helper
        # formatters already render a path-only / bold-title view when
        # the public URL is empty.
        self.paperless_url = os.environ.get("PAPERLESS_URL", "")
        self.paperless_token = os.environ.get("PAPERLESS_TOKEN", "")
        self.paperless_public_url = os.environ.get("PAPERLESS_PUBLIC_URL", "")
        self.code_url = os.environ.get("CODE_URL", "")
        self.code_public_url = os.environ.get("CODE_PUBLIC_URL", "")
        self.openai_url = os.environ.get("OPENAI_URL", "")
        self.openai_key = os.environ.get("OPENAI_KEY", "")
        self.language = os.environ.get("LANGUAGE", "en")
        # Per-bot settings from stacklet.toml [bots.archivist.settings]
        self.classify_enabled = settings.get("classify", True)
        self.reformat_enabled = settings.get("reformat", True)
        # Max OCR chars the classifier sees. The default is generous so
        # most docs classify on their full content; bump it in bot.toml
        # when running a larger-context model.
        self.classify_max_chars = int(settings.get(
            "classify_max_chars", DEFAULT_CLASSIFY_MAX_CHARS,
        ))
        # Org under which the shared knowledge vault (`<org>/memory`)
        # lives in Forgejo. The bot joins the org's Owners team so
        # admins can browse the repo from their dashboard.
        self.mirror_org = settings.get("mirror_org", "family")
        # Slug for the institutional bucket inside the vault (default
        # "family"). Sourced from stack.toml [core] shared_bucket and
        # rendered into the env as SHARED_BUCKET. Documents land at
        # <vault>/<shared_bucket>/documents/, correspondents at
        # <vault>/<shared_bucket>/correspondents/. Personal captures
        # route to the sender's own bucket.
        self.shared_bucket = os.environ.get("SHARED_BUCKET", "family")
        # Routing model: one room is the "documents" room and feeds the
        # Paperless pipeline. Every other room the bot is in — DMs,
        # per-person notes rooms, ad-hoc invites — runs the capture
        # pipeline instead. Alias is read from bot.toml so a deployment
        # that renames the docs room can update one line. Resolution
        # is per-event in `_room_context()` — see room_context.py for
        # why we don't pin a room id at startup.
        self.documents_room_alias = normalize_alias(
            settings.get("documents_room_alias", "documents"),
        )
        # Captures are bookmarks (URL pointer + LLM summary) by default —
        # the source URL is the truth, the digest is the marker. Flip
        # `capture_keep_body = true` in bot.toml to also archive the
        # extracted body alongside the digest. Notes (pasted text)
        # always keep the body regardless.
        self.capture_keep_body = bool(settings.get("capture_keep_body", False))
        # Top N capture tags fed into the capture prompt as a vocabulary
        # hint. 50 covers the long tail of a year-old vault; smaller
        # values would risk losing useful niche tags.
        self.capture_tag_prompt_size = int(
            settings.get("capture_tag_prompt_size", 50),
        )
        self._capture_tags: CaptureTagCache | None = None
        self._scan_sessions: dict[str, dict] = {}
        self._http: aiohttp.ClientSession | None = None
        self._paperless: PaperlessAPI | None = None
        self._classifier: Classifier | None = None
        self._url_extractor: UrlExtractor | None = None
        self._text_extractor: TextExtractor | None = None
        self._mirror: GitMirror | None = None
        self._pipeline: DocumentPipeline | None = None
        self._search: SearchService | None = None
        self._capture: CapturePipeline | None = None
        self._paperless_version: str = ""

    def t(self, key: str, **kwargs) -> str:
        return _t(self.language, key, **kwargs)

    def register_callbacks(self, client: AsyncClient) -> None:
        self.add_event_callback(self._on_file, (RoomMessageMedia, RoomMessageImage, RoomMessageFile))
        self.add_event_callback(self._on_text, RoomMessageText)

    async def start(self) -> None:
        logger.info("[archivist] Config: paperless={} openai={} language={} classify={} reformat={}",
                     self.paperless_url, self.openai_url, self.language,
                     self.classify_enabled, self.reformat_enabled)
        try:
            default_model = resolve_model(f"{self.name}/classifier")
            logger.info("[archivist] Model (classifier): {}", default_model)
        except ValueError as e:
            logger.warning("[archivist] {}", e)

        # Reuse the framework-owned aiohttp session (created here, before
        # super().start() blocks in the sync loop) — one pool, closed by
        # MicroBot on shutdown. Paperless / OpenAI / extractors share it.
        self._http = self._ensure_http()
        self._paperless = PaperlessAPI(self._http, self.paperless_url, self.paperless_token)
        # Vision-capability cache lives in the bot's data dir so a probe
        # done in one container restart isn't repeated by the next one.
        self._classifier = Classifier(
            self._http, self.openai_url, self.openai_key, bot_name=self.name,
            capabilities=ModelCapabilities(
                path=self._session_dir / "model-capabilities.json",
            ),
        )
        self._url_extractor = UrlExtractor(self._http)
        self._text_extractor = TextExtractor()
        self._capture_tags = CaptureTagCache(
            self._session_dir / "capture-tags.json",
        )
        self._capture_tags.load()
        logger.info(
            "[archivist] capture tag cache: {} tags (keep_body={})",
            len(self._capture_tags.top(10_000)),
            self.capture_keep_body,
        )
        # Always attempt to wire the memory vault writer. If
        # CODE_URL / admin creds aren't present, `_init_mirror`
        # leaves `self._mirror = None` and logs the reason; writes
        # become silent skips.
        self._init_mirror()
        self._pipeline = DocumentPipeline(
            paperless=self._paperless,
            classifier=self._classifier,
            mirror=self._mirror,
            bot_name=self.name,
            language=self.language,
            classify_enabled=self.classify_enabled,
            reformat_enabled=self.reformat_enabled,
            classify_max_chars=self.classify_max_chars,
            paperless_public_url=self.paperless_public_url,
            actor=self.user_id,
            load_ontology=self._load_ontology,
            correspondents_section=self._compute_correspondents_section,
        )
        self._search = SearchService(
            classifier=self._classifier,
            paperless=self._paperless,
            t=self.t,
            language=self.language,
            code_public_url=self.code_public_url,
            mirror_org=self.mirror_org,
            paperless_public_url=self.paperless_public_url,
            shared_bucket=self.shared_bucket,
            ontology_section=self._compute_ontology_section,
        )
        self._capture = CapturePipeline(
            url_extractor=self._url_extractor,
            text_extractor=self._text_extractor,
            classifier=self._classifier,
            mirror=self._mirror,
            capture_tags=self._capture_tags,
            paperless=self._paperless,
            bot_name=self.name,
            classify_max_chars=self.classify_max_chars,
            capture_keep_body=self.capture_keep_body,
            capture_tag_prompt_size=self.capture_tag_prompt_size,
        )
        # Warm the vision-capability cache on every boot. Previously
        # this was kicked from on_first_sync, but MicroBot only runs
        # that hook once across the lifetime of the welcome marker,
        # so a restart never re-probed. Probe results are cached to
        # disk inside Classifier, so this is a no-op if the cache
        # is already populated.
        if self.classify_enabled and self.openai_url:
            asyncio.create_task(self._classifier.has_vision())
        # The framework's start() owns the session loop and closes the
        # http session (via _aclose) on shutdown.
        await super().start()

    # ── Classifier prompt inputs (read fresh per call) ───────────────────
    #
    # Ontology and correspondents live on disk in the memory vault and are
    # hand-editable: in Obsidian, in Forgejo's web UI, or via the memory
    # CLI. We re-read both on every classification so a hand edit takes
    # effect on the next filed document — no restart, no caching gotcha.
    # The reads are cheap (small TOML, dozens of tiny markdown files);
    # the LLM call that follows dwarfs them.

    def _compute_ontology_section(self) -> str:
        """Render the classifier vocabulary block from the memory ontology.

        Reads `ontology.toml` from the memory stacklet's vault working
        copy (cloned + pulled by memory's own hooks). The bot-runner
        mounts the host data dir at `/data`, so the vault lives at
        `/data/memory/vault/` and `MEMORY_VAULT_DIR` points at it.

        Falls back to the shipped seed when the vault is missing
        (memory not installed yet, or first run on a host without a
        working copy).
        """
        return self._load_ontology().classifier_prompt_section(self.language)

    def _load_ontology(self):
        """Load the memory ontology (cached read; cheap TOML).

        Returned alongside the rendered prompt section so the pipeline
        can use the bilingual canonicals — `match_topics` /
        `match_doctype` normalize LLM output back to the household
        language and reject cross-field hallucinations through
        `Ontology.canonicalize_topic` / `canonicalize_doctype`.
        """
        vault_env = os.environ.get("MEMORY_VAULT_DIR", "")
        vault_path = Path(vault_env) if vault_env else None
        return _get_memory_ontology(vault_path)

    def _compute_correspondents_section(self) -> str:
        """Build the correspondents block from the memory vault.

        Each `<shared_bucket>/correspondents/*.md` page contributes a
        (canonical, aliases) line. Empty when the vault isn't seeded
        yet — the prompt falls back to the flat Paperless
        correspondents list, which carries no alias signal.
        """
        vault_env = os.environ.get("MEMORY_VAULT_DIR", "")
        if not vault_env:
            return ""
        correspondents = _load_memory_correspondents(
            Path(vault_env), self.shared_bucket,
        )
        return _memory_correspondents_section(correspondents)

    def _init_mirror(self) -> None:
        """Build a GitMirror if all required env is present.

        Soft-fails: missing env just disables the mirror for this run.
        The live reachability check happens inside `GitMirror.ensure_setup`
        on first publish.
        """
        code_url = os.environ.get("CODE_URL", "")
        admin_user = os.environ.get("MATRIX_ADMIN_USER", "")
        admin_password = os.environ.get("MATRIX_ADMIN_PASSWORD", "")
        admin_ids = os.environ.get("STACK_ADMIN_USER_IDS", "")

        if not (code_url and admin_user and admin_password):
            logger.info(
                "[archivist] Memory vault writer offline — "
                "CODE_URL or admin creds missing. Bring up `code` to enable."
            )
            return

        # @arthur:homestead.me → arthur
        admin_usernames = []
        for raw in admin_ids.split(","):
            raw = raw.strip()
            if not raw:
                continue
            name = raw.lstrip("@").split(":", 1)[0]
            if name and name != admin_user:
                admin_usernames.append(name)

        # `self._session_dir` is already the in-container path the bot
        # runner mounts (`/data/<stacklet>/bot`). Don't read DATA_DIR —
        # that env var carries the host path and would dump mirror state
        # outside the container's volume mount.
        self._mirror = GitMirror(
            code_url=code_url,
            admin_user=admin_user,
            admin_password=admin_password,
            admin_usernames=admin_usernames,
            data_dir=self._session_dir,
            org_name=self.mirror_org,
            shared_bucket=self.shared_bucket,
        )
        logger.info("[archivist] Memory vault writer: {} org={} (admins: {})",
                    code_url, self.mirror_org, ", ".join(admin_usernames) or "-")

    def _ai_status(self) -> str:
        if self.openai_url:
            return "🧠 **AI classification:** enabled — documents are tagged automatically."
        return "💡 **AI classification:** not configured. Run `stack up ai` to enable automatic tagging."


    def _duplicate_reply(self, name: str, e: PaperlessDuplicateError) -> str:
        """Render the 'already filed' chat reply for a Paperless duplicate.

        Points the user at the original doc's Paperless page so they can
        verify the match instead of wondering why the upload 'failed'.
        """
        link = (f"{self.paperless_public_url}/documents/{e.doc_id}/details"
                if e.doc_id and self.paperless_public_url else "")
        return self.t("already_filed",
                      name=name,
                      doc_id=e.doc_id if e.doc_id is not None else "?",
                      title=e.title or "(no title)",
                      link=link)

    async def on_first_sync(self) -> None:
        """One-time welcome broadcast to every joined room.

        MicroBot's welcome marker gates this hook to a single run across
        the lifetime of the bot's data directory — exactly what we want
        for the welcome message and nothing else. Anything that needs to
        happen on every boot (vision probe, documents-room resolution)
        belongs in `start()` or per-event `_room_context()`, not here.
        """
        welcome = self.t(
            "welcome",
            url=self.paperless_public_url,
            ai_status=self._ai_status(),
        )
        for room_id in self._client.rooms:
            await self._send(room_id, welcome)

    # ── Documents-room routing ───────────────────────────────────────────
    #
    # One room is the documents room: file uploads + URL pastes go through
    # Paperless. Every other room the bot is joined to runs the capture
    # pipeline — URLs and pasted text become summarized markdown entries
    # filed under the sender's own entity bucket (`<sender>/notes/...`
    # or `<sender>/bookmarks/...`), no Paperless write.
    #
    # Family members can spin up their own per-person rooms ("arthur
    # notes", "sabrina notes") or DM the bot directly. As soon as the
    # bot accepts the invite, that room is in capture mode by default.
    # No allowlist to curate.
    #
    # Resolution is per-event, not pinned at startup. The framework's
    # `_room_context` reads `room.canonical_alias` from the current nio
    # state on every callback; we compare against the configured alias
    # in the routing dispatcher. This survives restarts, alias renames,
    # and the documents room being joined late.

    def _is_documents_room(self, ctx) -> bool:
        """Archivist-specific routing flag: alias matches our docs alias.

        Sits next to the documents-room routing comment because that's
        what it's *for*; the framework's RoomContext stays generic and
        knows nothing about Paperless. An empty alias (deployments
        without Paperless) collapses every room to capture mode.
        """
        return bool(self.documents_room_alias) and ctx.alias == self.documents_room_alias

    # Paste detection lives in text_utils; exposed as a static method so
    # capture-room routing reads `self._looks_like_paste(...)`.
    _looks_like_paste = staticmethod(looks_like_paste)

    # ── Matrix helpers ───────────────────────────────────────────────────

    def _format_handler_error(self, event, exc: BaseException) -> str:
        """Localized override of the framework's error message hook.

        The framework wraps every event handler with a timeout + broad
        try/except and posts the result of this method into the room
        when something goes wrong. Both keys live in
        ``messages/archivist.yml`` so en / de stay in lockstep.
        """
        key = "handler_timeout" if isinstance(exc, asyncio.TimeoutError) else "handler_error"
        return self.t(key)

    # ── Document processing pipeline ─────────────────────────────────────

    @staticmethod
    def _event_date(event) -> str | None:
        """YYYY-MM-DD from a matrix-nio event's `server_timestamp`.

        Returned date is UTC. Used as the LLM's anchor for resolving
        partial dates on the document (the doc itself usually carries
        a date close to when the user uploaded it). Returns None when
        the event lacks a usable timestamp — caller falls back to the
        system date.
        """
        ts_ms = getattr(event, "server_timestamp", None)
        if not isinstance(ts_ms, (int, float)) or ts_ms <= 0:
            return None
        import datetime as _dt
        return _dt.datetime.fromtimestamp(
            ts_ms / 1000, tz=_dt.timezone.utc,
        ).date().isoformat()

    async def _reply_target_doc_id(self, room_id: str, event) -> int | None:
        """Return the paperless_id when `event` replies to one of OUR filings.

        The framework's `_reply_parent_envelope` does the transport work
        — fetch the replied-to parent, confirm it's ours, read off its
        `dev.famstack.event` envelope. Here we keep only the archivist's
        domain reading: a `document.filed` envelope carries the
        `paperless_id` the reprocess flow needs. Anything else (wrong
        type, no envelope, not a reply) routes normally.
        """
        envelope = await self._reply_parent_envelope(room_id, event)
        if not envelope or envelope.get("type") != "document.filed":
            return None
        paperless_id = envelope.get("data", {}).get("paperless_id")
        return paperless_id if isinstance(paperless_id, int) else None

    async def _handle_reply_reprocess(
        self, room_id: str, doc_id: int, user_hint: str, reply_to: str,
        *, date_filed: str | None = None,
    ) -> None:
        """Re-run the pipeline on `doc_id` with the user's reply as a hint.

        The hint becomes a high-priority block in the classify prompt —
        the LLM treats it as a correction that overrides its prior
        reading. The mirror's existing idempotent publish path
        overwrites the old markdown entry on the same paperless_id.
        `is_reprocess=True` preserves the document's filing date in
        Paperless.
        """
        doc = await self._paperless.get_doc(doc_id)
        if not doc:
            await self._send(
                room_id, self.t("reprocess_doc_missing", doc_id=doc_id),
                reply_to,
            )
            return

        ontology = self._load_ontology()
        result = await enrich_document(
            paperless=self._paperless,
            classifier=self._classifier,
            doc=doc,
            classify_max_chars=self.classify_max_chars,
            ontology_section=ontology.classifier_prompt_section(self.language),
            correspondents_section=self._compute_correspondents_section(),
            ontology=ontology,
            lang=self.language,
            is_reprocess=True,
            date_filed=date_filed,
            user_hint=user_hint,
        )
        if result.llm_error:
            kind, detail = result.llm_error
            await self._send(
                room_id,
                self.t("reprocess_llm_error", doc_id=doc_id, kind=kind, detail=detail),
                reply_to,
            )
            return

        # Refetch so the mirror sees the post-PATCH title/tags.
        refreshed = await self._paperless.get_doc(doc_id) or doc
        paperless_tags = [
            *result.resolved_topics,
            *(f"Person: {p}" for p in result.resolved_persons),
        ]
        await self._pipeline.safe_mirror(
            doc_id=doc_id,
            classification=dict(result.classification, **{
                "topics": result.resolved_topics,
                "persons": result.resolved_persons,
                "correspondent": result.resolved_correspondent,
                "document_type": result.resolved_type,
            }),
            body_text=refreshed.get("content", "") or "",
            processing="ai_formatted" if result.classification else "ocr",
            model=None,
            fallback_title=refreshed.get("title") or f"Paperless #{doc_id}",
            paperless_tags=paperless_tags,
            summary=result.summary,
        )

        # Echo back a confirmation carrying a fresh envelope so the
        # user can chain another correction by replying to THIS
        # message too. The envelope `type` is `document.reclassified`
        # so the deriver can distinguish the corrective pass from
        # the original filing in the ledger.
        title = result.classification.get("title") or refreshed.get("title") or f"#{doc_id}"
        meta_parts: list[str] = []
        meta_parts.extend(result.resolved_topics)
        meta_parts.extend(result.resolved_persons)
        if result.resolved_type:
            meta_parts.append(result.resolved_type)
        if result.resolved_correspondent:
            meta_parts.append(result.resolved_correspondent)
        lines = [self.t("reprocessed", title=title, doc_id=doc_id)]
        if meta_parts:
            lines.extend(["", "  " + " | ".join(meta_parts)])

        envelope = build_document_event(
            doc_id, result.classification,
            resolved_topics=result.resolved_topics,
            resolved_persons=result.resolved_persons,
            resolved_correspondent=result.resolved_correspondent,
            resolved_type=result.resolved_type,
            paperless_url=self.paperless_public_url,
            actor=self.user_id,
            ts=utc_now_isoformat(),
        )
        envelope["type"] = "document.reclassified"
        envelope["summary"] = f"{title} reclassified (#{doc_id})"
        envelope["data"]["user_hint"] = user_hint
        await self._send(
            room_id, "\n".join(lines), reply_to,
            metadata={"dev.famstack.event": envelope},
        )

    async def _process_document(
        self, room_id: str, filename: str, display_name: str,
        file_data: bytes, reply_to: str | None = None,
        date_filed: str | None = None,
        submitter_mxid: str | None = None,
    ):
        """File a document via the pipeline, then render the reply.

        Shared by all entry points: single file upload, multi-page scan,
        and URL archiving. The DocumentPipeline does the work (upload →
        OCR → classify → reformat → mirror → emit) and hands back a
        FilingOutcome; this maps the outcome to a chat reply and logs.
        """
        outcome = await self._pipeline.process(
            filename=filename, display_name=display_name, file_data=file_data,
            date_filed=date_filed, submitter_mxid=submitter_mxid,
        )
        await self._reply_for_outcome(room_id, outcome, reply_to)
        if outcome.status == "enriched":
            processed_parts = [*outcome.resolved_topics, *outcome.resolved_persons]
            if outcome.resolved_type:
                processed_parts.append(outcome.resolved_type)
            if outcome.resolved_correspondent:
                processed_parts.append(outcome.resolved_correspondent)
            logger.info(
                "[archivist] Processed: {} → doc {} [{}]",
                filename, outcome.doc_id,
                ", ".join(processed_parts) or "no-classification",
            )

    async def _reply_for_outcome(
        self, room_id: str, o: FilingOutcome, reply_to: str | None,
    ) -> None:
        """Map a FilingOutcome to a chat reply.

        Terminal statuses get a one-line message. The `enriched` status
        runs the historic priority chain — llm-error > no-text >
        classify-disabled > no-classification > the rich filed summary.
        That chain depends on chat-only inputs (openai_url, the
        translator) so it lives here, not in the pipeline.
        """
        if o.status == "upload_failed":
            await self._send(room_id, self.t("upload_failed", name=o.display_name), reply_to)
            return
        if o.status == "duplicate":
            await self._send(room_id, self._duplicate_reply(o.display_name, o.duplicate), reply_to)
            return
        if o.status == "ocr_failed":
            await self._send(room_id, self.t("ocr_failed", name=o.display_name), reply_to)
            return
        if o.status == "filed_no_details":
            await self._send(
                room_id, self.t("filed_no_details", name=o.display_name, link=o.link),
                reply_to,
            )
            return

        # status == "enriched": the document is filed; pick the reply.
        llm_error = _llm_error_for_chat(
            o.llm_error, name=o.display_name, openai_url=self.openai_url, link=o.link,
        )
        if llm_error:
            key, kwargs = llm_error
            await self._send(room_id, self.t(key, **kwargs), reply_to)
        elif not o.has_text:
            await self._send(
                room_id, self.t("filed_no_text", name=o.display_name, link=o.link),
                reply_to,
            )
        elif not o.classify_enabled:
            await self._send(
                room_id, f"{self.t('filed', title=o.display_name)}\n\n  {o.link}",
                reply_to,
            )
        elif not o.classification:
            await self._send(
                room_id, self.t("classify_failed", name=o.display_name, link=o.link),
                reply_to,
            )
        else:
            reply_text = render_filing_reply(
                self.t,
                display_title=o.classification.get("title") or o.display_name,
                doc_id=o.doc_id,
                resolved_topics=o.resolved_topics,
                resolved_persons=o.resolved_persons,
                resolved_type=o.resolved_type,
                resolved_correspondent=o.resolved_correspondent,
                date_applied=o.date_applied,
                classification=o.classification,
                created_new=o.created_new,
                reformat_failed=o.reformat_failed,
                link=o.link,
            )
            # The `dev.famstack.event` envelope rides on the visible
            # message — one replayable timeline event per filing.
            await self._send(
                room_id, reply_text, reply_to,
                metadata={"dev.famstack.event": o.envelope},
            )

    # ── Event handlers ───────────────────────────────────────────────────

    async def _on_file(self, room, event) -> None:
        if event.sender == self.user_id:
            return

        content = event.source.get("content", {})
        msgtype = content.get("msgtype", "")
        if msgtype not in SUPPORTED_MSGTYPES:
            return

        url = content.get("url", "")
        if not url or not url.startswith("mxc://"):
            return

        ctx = self._room_context(room)
        mentioned = self._is_bot_mentioned(event)
        if not self._should_react(ctx, mentioned=mentioned):
            logger.debug(
                "[archivist] skipping file from {} in {} per room mode",
                event.sender, ctx.room_id,
            )
            return

        raw_filename = content.get("body", "document")
        display_name = _clean_filename(raw_filename, msgtype)
        sender_name = event.sender.split(":")[0].replace("@", "").capitalize()
        reply_to = event.event_id

        # Multi-page scan mode
        if event.sender in self._scan_sessions:
            await self._handle_scan_page(room.room_id, event, url, raw_filename)
            return

        file_data = await self._download_media(url)
        if not file_data:
            await self._send(room.room_id, self.t("download_failed_matrix", name=display_name), reply_to)
            return

        if msgtype == "m.image":
            await self._send(room.room_id, self.t("received_photo", sender=sender_name), reply_to)
        else:
            await self._send(room.room_id, self.t("received_document", sender=sender_name), reply_to)

        # Start typing AFTER the confirmation message. Sending a chat
        # message clears the typing indicator on Element's side, so a
        # typing notice issued before the confirmation gets immediately
        # wiped by the message itself. Setting it here keeps the
        # indicator alive for the rest of the OCR + classify + mirror
        # work that follows.
        await self._set_typing(room.room_id, on=True)

        await self._process_document(
            room.room_id, raw_filename, display_name, file_data, reply_to,
            date_filed=self._event_date(event),
            submitter_mxid=event.sender,
        )

    async def _on_text(self, room, event: RoomMessageText) -> None:
        if event.sender == self.user_id:
            return

        query = event.body.strip()
        if not query:
            return
        query_lower = query.lower()
        reply_to = event.event_id

        ctx = self._room_context(room)
        mentioned = self._is_bot_mentioned(event)
        is_documents = self._is_documents_room(ctx)
        logger.info(
            "[archivist] text from {} in {} (alias={!r}, dm={}, docs={}, "
            "members={}, mention={})",
            event.sender, ctx.room_id, ctx.alias, ctx.is_dm,
            is_documents, len(ctx.members), mentioned,
        )
        if not self._should_react(ctx, mentioned=mentioned):
            logger.debug(
                "[archivist] skipping {} in {} per room mode",
                event.sender, ctx.room_id,
            )
            return

        # When the bot is mentioned, the mxid is conversational noise —
        # strip it so the remaining body drives command matching and
        # the search query. A bare ping with no content becomes "help"
        # so the user gets a useful response instead of an empty search.
        if mentioned:
            query = self.strip_mention(query, self.user_id)
            if not query:
                query = "help"
            query_lower = query.lower()

        # ── Reply-to-classification: user is correcting a prior filing ──
        # When the user replies to a bot's doc-filing message we can
        # trace back the doc_id from the parent event's metadata,
        # then re-run the classifier with the user's message as an
        # authoritative hint. Short-circuits before the rest of the
        # parser so a reply that happens to look like a search
        # ("ADAC") doesn't get routed to free-text search.
        doc_id = await self._reply_target_doc_id(room.room_id, event)
        if doc_id is not None:
            hint = _strip_reply_fallback(event.body)
            if hint:
                await self._handle_reply_reprocess(
                    room.room_id, doc_id, hint, reply_to,
                    date_filed=self._event_date(event),
                )
                return

        if query_lower in HELP_COMMANDS:
            await self._send(
                room.room_id,
                self.t(
                    "welcome",
                    url=self.paperless_public_url,
                    ai_status=self._ai_status(),
                ),
                reply_to,
            )

        elif query_lower in SCAN_BEGIN:
            sender_name = event.sender.split(":")[0].replace("@", "").capitalize()
            self._scan_sessions[event.sender] = {"files": [], "room_id": room.room_id}
            await self._send(room.room_id, self.t("scan_started", sender=sender_name), reply_to)

        elif query_lower in SCAN_END:
            if event.sender in self._scan_sessions:
                await self._handle_scan_complete(
                    room.room_id, event.sender, reply_to,
                    date_filed=self._event_date(event),
                )
            else:
                await self._send(room.room_id, self.t("no_active_scan"), reply_to)

        elif query_lower.startswith("show ") and query[5:].strip().isdigit():
            await self._handle_show(room.room_id, int(query[5:].strip()), reply_to)

        elif _is_just_url(query):
            # URL semantics don't change with mention — a URL in the
            # docs room means "ingest into Paperless"; anywhere else
            # it's a bookmark capture for the sender's bucket.
            if is_documents:
                await self._handle_url(
                    room.room_id, query, reply_to,
                    date_filed=self._event_date(event),
                    submitter_mxid=event.sender,
                )
            else:
                await self._handle_capture(
                    room.room_id, query, event.sender, reply_to,
                )

        elif mentioned or is_documents:
            # Free-text search runs whenever the user explicitly
            # addressed the bot (any room) or the message landed in
            # the documents room (where search is the default). A
            # short chat-shaped query like "ADAC" is fine — that's
            # exactly the kind of thing recall is for.
            await self._handle_search(
                room.room_id, query, reply_to,
                sender=event.sender,
            )

        elif self._looks_like_paste(query):
            # Capture room + paste-shaped message, no mention → file
            # as text capture. The user is dropping content into the
            # room; without an @-tag we treat it as material to keep,
            # not a question to answer.
            await self._handle_text_capture(
                room.room_id, query, event.sender, reply_to,
            )

        else:
            # Short message in a capture room — ignored. Pasting more
            # context will trigger capture; chat-shaped messages don't.
            logger.debug(
                "[archivist] capture room {} ignored short text: {!r}",
                room.room_id, query[:60],
            )


    # ── Scan mode ────────────────────────────────────────────────────────

    async def _handle_scan_page(self, room_id: str, event, url: str, raw_filename: str):
        reply_to = event.event_id
        try:
            file_data = await self._download_media(url)
        except Exception as e:
            await self._send(room_id, self.t("scan_page_failed", error=str(e)), reply_to)
            return

        if not file_data:
            await self._send(room_id, self.t("scan_page_failed_matrix"), reply_to)
            return

        session = self._scan_sessions[event.sender]
        session["files"].append((raw_filename, file_data))
        page_num = len(session["files"])
        await self._send(room_id, self.t("page_received", num=page_num), reply_to)

    async def _handle_scan_complete(
        self, room_id: str, sender: str, reply_to: str | None = None,
        *, date_filed: str | None = None,
    ):
        session = self._scan_sessions.pop(sender)
        files = session["files"]
        sender_name = sender.split(":")[0].replace("@", "").capitalize()

        if not files:
            await self._send(room_id, self.t("scan_cancelled"), reply_to)
            return

        if len(files) == 1:
            filename, file_data = files[0]
            display_name = _clean_filename(filename)
            await self._send(room_id, self.t("scan_complete_single"), reply_to)
            await self._process_document(
                room_id, filename, display_name, file_data, reply_to,
                date_filed=date_filed,
                submitter_mxid=sender,
            )
            return

        page_count = len(files)
        await self._send(room_id, self.t("scan_complete_multi", count=page_count), reply_to)

        try:
            pdf_data = _combine_images_to_pdf(files)
        except Exception as e:
            await self._send(room_id, self.t("scan_combine_failed", error=str(e)), reply_to)
            return

        filename = f"scan-{sender_name.lower()}-{page_count}p.pdf"
        display_name = f"scan ({page_count} pages)"
        await self._process_document(
            room_id, filename, display_name, pdf_data, reply_to,
            date_filed=date_filed,
            submitter_mxid=sender,
        )

    # ── URL capture (knowledge rooms, DMs, per-person notes rooms) ───────
    #
    # Capture mode is what runs when the bot sees a URL in any room that
    # isn't the documents room. The flow is intentionally lean compared
    # to `_process_document`:
    #
    #   UrlExtractor → SourceContent → classifier → mirror.publish_capture
    #
    # No Paperless write, no entity reconciliation, no event emission.
    # The classifier still uses the memory ontology + correspondents wiki
    # so captures share a vocabulary with Paperless mirrors and can be
    # cross-referenced in Obsidian.
    #
    # Person attribution: the sender's first name is the default. The
    # classifier can return a wider `persons` list if the article body
    # mentions multiple family members, but for v1 the sender is whose
    # capture this is — the rule is "you pasted it, it's yours."

    def _notifier(self, room_id: str, reply_to: str | None) -> MatrixNotifier:
        """A Notifier bound to this room + reply thread for mid-flow status."""
        return MatrixNotifier(
            room_id=room_id, reply_to=reply_to, send=self._send, t=self.t,
        )

    async def _handle_capture(
        self, room_id: str, url: str, sender_mxid: str,
        reply_to: str | None = None,
    ) -> None:
        outcome = await self._capture.capture_url(
            url=url, sender_mxid=sender_mxid,
            notifier=self._notifier(room_id, reply_to),
        )
        await self._reply_for_capture(room_id, outcome, reply_to)

    async def _handle_text_capture(
        self, room_id: str, text: str, sender_mxid: str,
        reply_to: str | None = None,
    ) -> None:
        outcome = await self._capture.capture_text(
            text=text, sender_mxid=sender_mxid,
        )
        await self._reply_for_capture(room_id, outcome, reply_to)

    async def _reply_for_capture(
        self, room_id: str, o: CaptureOutcome, reply_to: str | None,
    ) -> None:
        """Map a CaptureOutcome to a chat reply.

        `empty` (whitespace-only paste) is a silent drop; terminal
        statuses get a one-line message; a capture renders via the
        shared presenter.
        """
        if o.status == "empty":
            return
        if o.status == "extract_failed":
            await self._send(room_id, self.t("capture_failed"), reply_to)
            return
        if o.status == "no_mirror":
            await self._send(room_id, self.t("capture_no_mirror"), reply_to)
            return
        reply = render_capture_reply(
            self.t,
            source_title_hint=o.source_title_hint,
            classification=o.classification,
            link=o.display_link,
        )
        await self._send(room_id, reply, reply_to)

    # ── URL archiving (documents room — feeds Paperless) ─────────────────

    async def _handle_url(
        self, room_id: str, url: str, reply_to: str | None = None,
        *, date_filed: str | None = None, submitter_mxid: str | None = None,
    ):
        google_export = _google_docs_export_url(url)
        if google_export:
            download_url, doc_type = google_export
            type_labels = {"document": "Google Doc", "spreadsheets": "Google Sheet", "presentation": "Google Slides"}
            await self._send(room_id, self.t("downloading_google", type=type_labels.get(doc_type, "Google Doc")), reply_to)
        else:
            download_url = url
            await self._send(room_id, self.t("downloading_url"), reply_to)

        try:
            async with self._http.get(download_url, timeout=aiohttp.ClientTimeout(total=60), allow_redirects=True) as resp:
                if resp.status != 200:
                    await self._send(room_id, self.t("url_http_error", status=resp.status), reply_to)
                    return
                file_data = await resp.read()
                content_type = resp.content_type or ""
        except asyncio.TimeoutError:
            await self._send(room_id, self.t("url_timeout"), reply_to)
            return
        except aiohttp.ClientError as e:
            await self._send(room_id, self.t("url_error", error=str(e)), reply_to)
            return

        if not file_data:
            await self._send(room_id, self.t("url_empty"), reply_to)
            return

        # Determine filename
        if google_export:
            filename = f"google-{doc_type}.pdf"
            display_name = type_labels.get(doc_type, "Google Doc")
        elif "pdf" in content_type or url.lower().endswith(".pdf"):
            url_path = url.split("?")[0].split("#")[0]
            filename = url_path.rsplit("/", 1)[-1] if "/" in url_path else "document.pdf"
            if not filename.lower().endswith(".pdf"):
                filename = "document.pdf"
            display_name = filename
        elif file_data[:5] == b'%PDF-':
            filename = display_name = "document.pdf"
        else:
            await self._send(room_id, self.t("url_not_pdf", content_type=content_type), reply_to)
            return

        await self._process_document(
            room_id, filename, display_name, file_data, reply_to,
            date_filed=date_filed,
            submitter_mxid=submitter_mxid,
        )

    # ── Search ───────────────────────────────────────────────────────────

    async def _handle_search(
        self, room_id: str, query: str, reply_to: str | None = None,
        *, sender: str | None = None,
    ):
        """Search Paperless + the memory vault and reply.

        Delegates to SearchService for the work (query resolution, dual
        search, synthesis + bounded deep-dive); it returns the reply
        text. Search posts no confirmation before the work, so typing
        starts immediately and the framework's wrap clears it on exit.
        The one mid-flow status (the deep-dive "looking deeper" note) is
        sent through the `announce` callback.
        """
        await self._set_typing(room_id, on=True)

        reply = await self._search.run(
            query=query, sender=sender,
            notifier=self._notifier(room_id, reply_to),
        )
        await self._send(room_id, reply, reply_to)

    # ── Show document content ─────────────────────────────────────────

    async def _handle_show(self, room_id: str, doc_id: int, reply_to: str | None = None):
        """Fetch a document's content from Paperless and return it as Markdown."""
        doc = await self._paperless.get_doc(doc_id)
        if not doc:
            await self._send(room_id, f"Document #{doc_id} not found.", reply_to)
            return

        title = doc.get("title", "Untitled")
        content = doc.get("content", "").strip()
        link = f"{self.paperless_public_url}/documents/{doc_id}/details" if self.paperless_public_url else ""

        if not content:
            await self._send(room_id, f"**{title}** — no text content available.\n\n  {link}", reply_to)
            return

        # Matrix has message size limits — truncate long documents
        if len(content) > 4000:
            content = content[:4000] + "\n\n[... truncated]"

        lines = [f"**{title}**", "", content]
        if link:
            lines.extend(["", link])

        await self._send(room_id, "\n".join(lines), reply_to)
