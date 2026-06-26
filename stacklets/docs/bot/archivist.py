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
    ReactionEvent,
    RoomMessageMedia,
    RoomMessageImage,
    RoomMessageFile,
    RoomMessageAudio,
    RoomMessageText,
)

from capture_tags import CaptureTagCache
from extractors import TextExtractor, UrlExtractor
from git_mirror import GitMirror
from microbot import EYES, MicroBot
from pdf_analysis import (
    DEFAULT_REFORMAT_MAX_PDF_PAGES,
    DEFAULT_VISION_MAX_PDF_PAGES,
)
from pipeline import (
    Classifier,
    DEFAULT_CLASSIFY_MAX_CHARS,
    PaperlessAPI,
    PaperlessDuplicateError,
)
from stack import resolve_model
from stack.email_message import defang_links
from stack.ai.client import (
    LLMError,
    LLMUnavailableError,
    ModelCapabilities,
    Transcriber,
)

# Make sibling stacklets importable. In the bot-runner container,
# `/stacklets/` is mounted read-only and holds all stacklets; locally
# it's the source tree. Either way, the path resolves the same way.
_STACKLETS_DIR = Path(__file__).resolve().parents[2]
if str(_STACKLETS_DIR) not in sys.path:
    sys.path.insert(0, str(_STACKLETS_DIR))
from room_context import normalize_alias  # noqa: E402
from text_utils import (  # noqa: E402
    attachment_caption as _attachment_caption,
    clean_filename as _clean_filename,
    first_url as _first_url,
    google_docs_export_url as _google_docs_export_url,
    is_just_url as _is_just_url,
    join_captions as _join_captions,
    looks_like_paste,
    split_scan_command as _split_scan_command,
    strip_reply_fallback as _strip_reply_fallback,
)
from reply_presenter import (  # noqa: E402
    render_capture_reply,
    render_filing_reply,
    render_reprocessed_reply,
)
from document_pipeline import (  # noqa: E402
    DocumentPipeline,
    FilingOutcome,
    utc_now_isoformat,
)
from search_service import SearchService  # noqa: E402
from capture_pipeline import CapturePipeline, CaptureOutcome  # noqa: E402
from notifier import MatrixNotifier  # noqa: E402
from topic_rooms import (  # noqa: E402
    TopicBinding,
    binding_from_state,
    is_reserved,
    make_room_state,
    parse_topic_name,
    scope_from_members,
)
from vault_context import VaultContext  # noqa: E402


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

SUPPORTED_MSGTYPES = {"m.file", "m.image", "m.audio"}

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

_MIME_BY_EXT = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "png": "image/png",
    "heic": "image/heic", "heif": "image/heif",
    "webp": "image/webp",
    "tiff": "image/tiff", "tif": "image/tiff",
    "md": "text/markdown", "markdown": "text/markdown",
    "txt": "text/plain",
    # Voice messages: Element and most Matrix clients upload Opus-in-OGG
    # for the in-app voice recorder; mp3 / m4a / wav cover files attached
    # from elsewhere. Element X uses .ogg specifically.
    "ogg": "audio/ogg", "oga": "audio/ogg", "opus": "audio/ogg",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4", "mp4a": "audio/mp4",
    "wav": "audio/wav",
    "webm": "audio/webm",
}


def _guess_mime(filename: str, msgtype: str) -> str:
    """Best-effort MIME from filename extension, with msgtype as a
    fallback ("m.image" → image/jpeg). Used when the upload event
    omits `content.info.mimetype`, which happens on some clients."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in _MIME_BY_EXT:
        return _MIME_BY_EXT[ext]
    if msgtype == "m.image":
        return "image/jpeg"
    if msgtype == "m.audio":
        return "audio/ogg"
    return "application/octet-stream"


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
        # Voice transcription endpoint. When unset (e.g. the AI stacklet
        # isn't installed yet), the archivist still files PDFs/images; only
        # the audio-capture path becomes a soft-skip with a friendly reply.
        self.whisper_url = os.environ.get("WHISPER_URL", "")
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
        # Page caps on PDF vision/reformat. Defaults are safe for small
        # context models (32K-ish); raise both together when running a
        # larger-context LLM so longer docs still get the full vision
        # pass and layout-recovery reformat. A 1-3K-token-per-page
        # render means 5 pages already eats ~15K input tokens, so tune
        # to your model's headroom.
        self.vision_max_pdf_pages = int(settings.get(
            "vision_max_pdf_pages", DEFAULT_VISION_MAX_PDF_PAGES,
        ))
        self.reformat_max_pdf_pages = int(settings.get(
            "reformat_max_pdf_pages", DEFAULT_REFORMAT_MAX_PDF_PAGES,
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
        # Rooms known to be welcomed this session — caches the
        # own-messages history query in `_send_room_welcome_if_needed`
        # so each room pays it at most once per process lifetime.
        self._welcomed_rooms: set[str] = set()
        # Topic bindings resolved this session — caches the
        # own-messages history query in `_topic_binding` the same way.
        self._topic_bindings: dict[str, TopicBinding] = {}
        self._http: aiohttp.ClientSession | None = None
        self._paperless: PaperlessAPI | None = None
        self._classifier: Classifier | None = None
        self._url_extractor: UrlExtractor | None = None
        self._text_extractor: TextExtractor | None = None
        self._mirror: GitMirror | None = None
        self._pipeline: DocumentPipeline | None = None
        self._search: SearchService | None = None
        self._capture: CapturePipeline | None = None
        self._transcriber: Transcriber | None = None
        self._vault: VaultContext | None = None
        self._paperless_version: str = ""

    def t(self, key: str, **kwargs) -> str:
        return _t(self.language, key, **kwargs)

    def register_callbacks(self, client: AsyncClient) -> None:
        self.add_event_callback(
            self._on_file,
            (RoomMessageMedia, RoomMessageImage, RoomMessageFile, RoomMessageAudio),
        )
        self.add_event_callback(self._on_text, RoomMessageText)
        # User → bot reactions: 🔖 to save the reacted message. The drain
        # delivers reactions as typed ReactionEvents (verified on the rig).
        self.add_event_callback(self._on_reaction, ReactionEvent)

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
        # When OPENAI_URL is empty, leave the classifier unconfigured —
        # the bot still does filing / search / URL archiving, and the
        # framework refuses to construct against a missing endpoint so
        # we don't silently leak documents to api.openai.com.
        if self.openai_url:
            self._classifier = Classifier.from_endpoint(
                self.openai_url, self.openai_key, bot_name=self.name,
                capabilities=ModelCapabilities(
                    path=self._session_dir / "model-capabilities.json",
                ),
            )
        elif self.classify_enabled:
            logger.warning(
                "[archivist] classify=true but OPENAI_URL is empty — "
                "disabling classification; set up AI with 'stack up ai'"
            )
            self.classify_enabled = False
            self.reformat_enabled = False
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
        self._vault = VaultContext(language=self.language, shared_bucket=self.shared_bucket)
        self._pipeline = DocumentPipeline(
            paperless=self._paperless,
            classifier=self._classifier,
            mirror=self._mirror,
            bot_name=self.name,
            language=self.language,
            classify_enabled=self.classify_enabled,
            reformat_enabled=self.reformat_enabled,
            classify_max_chars=self.classify_max_chars,
            vision_max_pdf_pages=self.vision_max_pdf_pages,
            reformat_max_pdf_pages=self.reformat_max_pdf_pages,
            paperless_public_url=self.paperless_public_url,
            actor=self.user_id,
            vault=self._vault,
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
            vault=self._vault,
        )
        # Whisper speaks /v1/audio/transcriptions on its own port, so the
        # Transcriber gets its own client. When WHISPER_URL is unset the
        # archivist still works for PDFs/images; only the voice-capture
        # branch in the pipeline soft-skips.
        if self.whisper_url:
            try:
                self._transcriber = Transcriber.from_env(namespace=self.name)
            except LLMUnavailableError as e:
                logger.warning("[archivist] no transcription: {}", e)
                self._transcriber = None
        else:
            logger.info(
                "[archivist] WHISPER_URL unset — voice messages will be ignored "
                "(set up AI with 'stack up ai' to enable transcription)"
            )

        # The capture pipeline borrows the classifier's framework LLM for
        # transcript cleanup -- punctuating raw whisper output with the
        # model that's already running. When the classifier isn't built
        # (AI not configured), cleanup soft-skips and the raw transcript
        # falls through.
        capture_llm = self._classifier.llm if self._classifier is not None else None
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
            vision_max_pdf_pages=self.vision_max_pdf_pages,
            transcriber=self._transcriber,
            llm=capture_llm,
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

        # @homer:homestead.me → homer
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
        """Walk every joined room and welcome the ones the bot has not
        greeted yet, picking the variant that fits each room's kind.

        On a fresh install this delivers the introduction to every
        room the bot was invited to before its first sync completed
        (typically the documents room from the seed install). On bot
        restart the own-messages history gate short-circuits this
        loop -- rooms that were already welcomed in a prior run stay
        silent.

        For rooms invited after first install, the eager path is
        ``on_room_joined`` (fires from MicroBot's auto-accept of an
        invite) plus the per-event fallback in `_on_text` / `_on_file`.
        Both routes converge on the same idempotent
        ``_send_room_welcome_if_needed`` orchestrator.
        """

        for room_id in list(self._client.rooms):
            room = self._client.rooms[room_id]
            ctx = self._room_context(room)
            try:
                await self._send_room_welcome_if_needed(room, ctx)
            except Exception as e:
                logger.warning(
                    "[archivist] welcome on first sync failed for {}: {}",
                    room_id, e,
                )

    async def on_room_joined(self, room_id: str) -> None:
        """Post the per-room welcome.

        The framework's deferred-notification path guarantees room
        state is populated when this fires; we don't need to poll
        ourselves. The per-event path in `_on_text` / `_on_file`
        remains the safety net for any edge case the deferred path
        doesn't reach.
        """

        room = self._room_by_id(room_id)
        if room is None:
            return
        ctx = self._room_context(room)
        try:
            await self._send_room_welcome_if_needed(room, ctx)
        except Exception as e:
            logger.warning(
                "[archivist] welcome on join failed for {}: {}", room_id, e,
            )

    # ── Documents-room routing ───────────────────────────────────────────
    #
    # One room is the documents room: file uploads + URL pastes go through
    # Paperless. Every other room the bot is joined to runs the capture
    # pipeline — URLs and pasted text become summarized markdown entries
    # filed under the sender's own entity bucket (`<sender>/notes/...`
    # or `<sender>/bookmarks/...`), no Paperless write.
    #
    # Family members can spin up their own per-person rooms ("homer
    # notes", "marge notes") or DM the bot directly. As soon as the
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

    # ── Topic-room routing ─────────────────────────────────────────────
    #
    # A Matrix room whose name starts with `Thema:` (de) or `Topic:` (en)
    # is a topic room. The archivist reads `dev.famstack.capture` state on
    # every capture; when no state exists yet, it parses the room name
    # and bootstraps inline (the lazy path). Future on_invite hook can
    # call the same bootstrap eagerly. Either way the result is a
    # `TopicBinding` the capture pipeline threads into routing + tag
    # seeding. Full design: docs/design/brain/topic-rooms.md.

    def _reserved_topic_slugs(self) -> set[str]:
        """Slugs the archivist refuses to bootstrap as topics.

        Topics nest inside their owning bucket (shared under
        `<shared_bucket>/`, personal under `<localpart>/`), so the
        slug only has to avoid collisions with within-bucket reserved
        directories: the capture-type folders the mirror writes
        (`notes`, `bookmarks`, `documents`), the correspondent index
        used in the shared bucket, the `_unfiled` rescue folder, and
        the derived `about` page.

        Top-level vault names (`family`, `homer`, `marge`, `meta`,
        `wiki`, `archive`) no longer need to appear here -- the new
        layout makes top-level collisions impossible because topic
        folders never live at the top level.
        """

        return {
            "notes", "bookmarks", "documents",
            "correspondents", "_unfiled", "about",
        }

    async def _read_topic_state(self, room_id: str) -> dict | None:
        """Fetch the room's `dev.famstack.capture` binding, or None.

        The binding lives as the bot's own custom TIMELINE event, not
        room state: message events send at PL 0, while state events
        need PL 50 — which the bot never has in a user-created topic
        room, so a state-event binding silently failed to persist and
        the scope re-derived (and could flap) on every capture. Read
        back via a sender+type-filtered `/messages` page, newest
        first, so the latest binding wins if the room is ever rebound
        (future promotion handler). None on miss or any nio error;
        bootstrap treats both as "no binding yet".
        """

        if self._client is None:
            return None
        try:
            resp = await self._client.room_messages(
                room_id,
                limit=3,
                message_filter={
                    "senders": [self.user_id],
                    "types": ["dev.famstack.capture"],
                },
            )
        except Exception as e:
            logger.debug(
                "[archivist] topic binding read failed for {}: {}",
                room_id, e,
            )
            return None
        for ev in getattr(resp, "chunk", None) or []:
            source = getattr(ev, "source", None) or {}
            content = source.get("content")
            if isinstance(content, dict):
                return content
        return None

    async def _write_topic_state(self, room_id: str, content: dict) -> bool:
        """Post the `dev.famstack.capture` binding as the bot's own
        timeline event. True on success, False on any nio error (the
        bootstrap is best-effort; the binding still applies in memory
        for the current capture). Custom event types are invisible in
        Element, so the room stays clean."""

        if self._client is None:
            return False
        try:
            await self._client.room_send(
                room_id, "dev.famstack.capture", content,
            )
            return True
        except Exception as e:
            logger.warning(
                "[archivist] topic binding write failed for {}: {}",
                room_id, e,
            )
            return False

    def _human_members(self, room) -> list[str]:
        """Room members that are humans — every bot account excluded.

        Uses the framework's `-bot` convention (MicroBot.is_bot_user), so a
        room with mail-bot + archivist-bot + one person counts as one human,
        not three. This is the basis of scope/visibility: one human → personal,
        two or more → shared.
        """
        users = getattr(room, "users", None) or {}
        return [u for u in users if not self.is_bot_user(u)]

    def _count_humans_in_room(self, room) -> int:
        return len(self._human_members(room))

    def _scope_owner_localpart(self, room, sender_mxid: str, scope) -> str:
        """Localpart that owns a personal-scope topic bucket.

        For a human paste the sender *is* the sole human, so the sender works.
        But a bot-posted source (email) has a bot sender — so a personal topic
        must nest under the room's lone human, not the bot. Resolve that here:
        the single human member when personal, else the sender (shared scope,
        or any non-single-human fallback).
        """
        if scope == "personal":
            humans = self._human_members(room)
            if len(humans) == 1:
                return humans[0].split(":")[0].lstrip("@").lower()
        return sender_mxid.split(":")[0].lstrip("@").lower()

    def _scope_bucket(self, room) -> str:
        """Bucket for bot-posted content from a room with no topic binding.

        The membership rule without a topic slug: a sole human (a DM, a
        one-person private room) files under that person; two or more humans
        file under the shared bucket. This is what makes email delivered to a
        DM land in that person's bucket — a DM is just a room with one human.
        Human-sent captures keep their own sender-based routing; this is only
        the fallback for bot-posted content (which has a bot sender).
        """
        humans = self._human_members(room)
        if len(humans) == 1:
            return humans[0].split(":")[0].lstrip("@").lower()
        return self.shared_bucket

    async def _topic_binding(self, room, sender_mxid: str) -> TopicBinding | None:
        """Read existing topic state, or bootstrap if the room name
        matches the prefix pattern.

        Returns the routing binding for the capture pipeline, or None
        when the room isn't a topic room (no matching name, no state).
        Bootstrap is idempotent and best-effort: a write failure
        leaves the in-memory binding live for the current capture and
        the next one re-tries.
        """

        if room is None:
            return None
        cached = self._topic_bindings.get(room.room_id)
        if cached is not None:
            return cached
        state = await self._read_topic_state(room.room_id)
        binding = binding_from_state(state)
        if binding is not None:
            self._topic_bindings[room.room_id] = binding
            return binding

        room_name = (
            getattr(room, "name", None)
            or getattr(room, "display_name", None)
            or ""
        )
        parsed = parse_topic_name(room_name)
        if parsed is None:
            return None
        if is_reserved(parsed.slug, self._reserved_topic_slugs()):
            logger.info(
                "[archivist] refusing topic bootstrap: slug {!r} from "
                "room {!r} collides with a reserved bucket name",
                parsed.slug, parsed.display_name,
            )
            return None

        scope = scope_from_members(self._count_humans_in_room(room))
        # Personal scope nests under the room's sole human, not the message
        # sender — so a bot-posted email in a one-person room lands under that
        # person, not under @mail-bot.
        owner_localpart = self._scope_owner_localpart(room, sender_mxid, scope)
        content = make_room_state(
            parsed=parsed, scope=scope,
            bootstrapped_by=sender_mxid,
            sender_localpart=owner_localpart,
            shared_bucket=self.shared_bucket,
            bootstrapped_at=utc_now_isoformat(),
        )
        wrote = await self._write_topic_state(room.room_id, content)
        logger.info(
            "[archivist] bootstrapped topic {!r} → bucket {!r} (scope={}, "
            "bootstrapped_by={})",
            parsed.display_name, content["bucket"], scope, sender_mxid,
        )
        binding = binding_from_state(content)
        # Cache only when the binding event landed: a failed write must
        # leave the next capture free to re-bootstrap and retry it.
        if binding is not None and wrote:
            self._topic_bindings[room.room_id] = binding
        return binding

    def _room_by_id(self, room_id: str):
        """Look up the live nio Room object for a given room id.

        Capture handlers receive ``room_id`` (a string) from their
        dispatchers; the topic bootstrap needs the Room object for
        its display name and member list. Returns None when the
        client isn't connected or the room isn't tracked locally.
        """

        if self._client is None:
            return None
        rooms = getattr(self._client, "rooms", None) or {}
        return rooms.get(room_id)

    # ── Per-room welcome ─────────────────────────────────────────────
    #
    # The archivist greets every room it enters with a context-aware
    # welcome (documents / topic / DM / generic capture) the first time
    # it sees an event there. The gate is the room's own history: a
    # sender-filtered `/messages` query asks Synapse whether the bot
    # has ever posted in the room. A read needs no power level, so the
    # gate works in user-created rooms where the bot (PL 0) cannot
    # write state events -- the failure mode of the previous
    # state-event gate, which silently re-welcomed on every message.
    # An in-memory cache keeps it to one history query per room per
    # process lifetime. The `help` / `hilfe` command serves the same
    # welcome text directly so the user re-reads exactly what fits
    # where they are asking. Project rule: self-explaining UX, see
    # memory `project_self_explaining_ux.md`.

    @staticmethod
    def _room_display_name(room) -> str:
        return (
            getattr(room, "name", None)
            or getattr(room, "display_name", None)
            or ""
        )

    def _welcome_kind_for(self, room, ctx) -> str:
        """Decide which welcome variant fits this room.

        Topic rooms win over every other classification: a `Thema:` /
        `Topic:` prefix is the strongest signal of user intent and
        overrides documents-room aliases or DM shape. The remaining
        order is documents > personal (DM) > capture (fallback).
        """

        name = self._room_display_name(room)
        if parse_topic_name(name) is not None:
            return "topic"
        if self._is_documents_room(ctx):
            return "documents"
        if getattr(ctx, "is_dm", False):
            return "personal"
        return "capture"

    def _welcome_text_for(self, room, ctx) -> str:
        """Render the welcome text for this room's kind, filling vars.

        The documents and personal variants reuse the existing welcome
        plumbing (ai_status, paperless URL); the topic variant computes
        the bucket path the family will see for captures here so the
        welcome doubles as a navigation hint to Forgejo.
        """

        kind = self._welcome_kind_for(room, ctx)
        if kind == "topic":
            parsed = parse_topic_name(self._room_display_name(room))
            scope = scope_from_members(self._count_humans_in_room(room))
            if scope == "shared":
                bucket = f"{self.shared_bucket}/{parsed.slug}"
            else:
                users = getattr(room, "users", None) or {}
                humans = [u for u in users if u != self.user_id]
                if humans:
                    localpart = humans[0].split(":")[0].lstrip("@").lower()
                    bucket = f"{localpart}/{parsed.slug}"
                else:
                    bucket = parsed.slug
            return self.t(
                "welcome_topic",
                display=parsed.display_name,
                slug=parsed.slug,
                bucket=bucket,
            )
        if kind == "documents":
            return self.t(
                "welcome_documents",
                url=self.paperless_public_url,
                ai_status=self._ai_status(),
            )
        if kind == "personal":
            return self.t("welcome_personal")
        return self.t("welcome_capture")

    async def _bot_has_posted_in(self, room_id: str) -> bool:
        """True when the bot's own messages already exist in the room.

        One sender-filtered `/messages` page, scanned backwards from
        the latest event — the homeserver does the filtering, so an
        active room does not force deep pagination. Returns False on
        any nio error: a transient hiccup then re-welcomes (rare,
        visible, recoverable), which beats silently never greeting a
        brand-new room.
        """

        if self._client is None:
            return False
        try:
            resp = await self._client.room_messages(
                room_id,
                limit=3,
                message_filter={
                    "senders": [self.user_id],
                    "types": ["m.room.message"],
                },
            )
        except Exception as e:
            logger.debug(
                "[archivist] welcome history check failed for {}: {}",
                room_id, e,
            )
            return False
        chunk = getattr(resp, "chunk", None) or []
        return any(
            getattr(ev, "sender", None) == self.user_id for ev in chunk
        )

    async def _send_room_welcome_if_needed(self, room, ctx) -> None:
        """Post the room-appropriate welcome on first encounter.

        Idempotent without write permission: the gate asks the
        homeserver whether the bot has posted in the room before. The
        in-memory cache is marked before the send, so a burst of
        events in a fresh room cannot produce a second welcome while
        the first send is in flight.
        """

        if room is None:
            return
        room_id = room.room_id
        if room_id in self._welcomed_rooms:
            return
        if await self._bot_has_posted_in(room_id):
            self._welcomed_rooms.add(room_id)
            return
        self._welcomed_rooms.add(room_id)
        text = self._welcome_text_for(room, ctx)
        await self._send(room_id, text)

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
        domain reading: any envelope that carries a `paperless_id` is a
        valid correction target. That covers the initial `document.filed`
        message AND a later `document.reclassified` -- the user can chain
        corrections by replying to the most recent classification reply,
        not just to the original filing.
        """
        envelope = await self._reply_parent_envelope(room_id, event)
        if not envelope or envelope.get("type") not in (
            "document.filed", "document.reclassified",
        ):
            return None
        paperless_id = envelope.get("data", {}).get("paperless_id")
        return paperless_id if isinstance(paperless_id, int) else None

    async def _reply_target_capture_path(
        self, room_id: str, event,
    ) -> str | None:
        """Return the capture's vault path when `event` replies to one
        of OUR captures.

        Mirrors `_reply_target_doc_id` exactly -- accepts both the
        initial `capture.filed` envelope and any later
        `capture.reclassified` so chained corrections work the same
        way they do for documents.
        """
        envelope = await self._reply_parent_envelope(room_id, event)
        if not envelope or envelope.get("type") not in (
            "capture.filed", "capture.reclassified",
        ):
            return None
        vault_path = envelope.get("data", {}).get("vault_path")
        return vault_path if isinstance(vault_path, str) and vault_path else None

    async def _collect_correction_chain(
        self, room_id: str, event,
    ) -> tuple[str, dict | None]:
        """Walk the reply chain back to the original filing; return both
        the joined human-correction hint AND the latest classification
        state the user was looking at when they wrote this correction.

        Each round of correction → reclassification adds two layers to
        the thread (user reply, bot's reclassified confirmation). We
        walk those layers in pairs, collecting every user turn between
        the current event and the bot's `*.filed` boundary -- beyond
        that boundary the human's words belong to the upload's
        caption, not to a correction.

        The latest envelope (the IMMEDIATE parent the user just
        replied to) carries the post-correction-N classification under
        ``data``. That's the state the human saw on screen when they
        typed their note, so it's the right anchor for "apply this
        correction as a delta": the LLM works against the same picture
        the user saw, not against the LLM's untouched first pass.
        Each step (state_N + correction_(N+1) → state_(N+1)) is
        deterministic; chaining them gives a deterministic transform
        from the initial filing to the current correction.

        Returned hint: numbered list when more than one correction is
        present, plain string for a single correction.
        """
        bodies: list[str] = []
        current_body = _strip_reply_fallback(event.body)
        if current_body:
            bodies.append(current_body)

        # The IMMEDIATE parent is the latest classification the user
        # replied to. Grab its envelope BEFORE walking back so we can
        # hand it to the prompt as the delta-anchor; the walker itself
        # only needs the chain of human turns.
        latest_state: dict | None = None
        parent_id = self._in_reply_to_id(event)
        immediate = (
            await self._fetch_event(room_id, parent_id) if parent_id else None
        )
        if immediate is not None and getattr(immediate, "sender", None) == self.user_id:
            envelope = immediate.source.get("content", {}).get(self.FAMSTACK_EVENT_KEY)
            if isinstance(envelope, dict):
                data = envelope.get("data")
                if isinstance(data, dict):
                    latest_state = data

        # Each loop iteration consumes one (bot, prior-user) pair from
        # the chain. Bounded by the framework's in_reply_to depth and a
        # hard cap so a thread that somehow loops doesn't burn API
        # calls forever.
        for _ in range(10):
            if not parent_id:
                break
            parent = await self._fetch_event(room_id, parent_id)
            if parent is None or getattr(parent, "sender", None) != self.user_id:
                break
            envelope = parent.source.get("content", {}).get(self.FAMSTACK_EVENT_KEY)
            if not isinstance(envelope, dict):
                break
            # Generic over filing kind: any `*.filed` is the boundary,
            # any `*.reclassified` is an intermediate hop. So the same
            # walker handles `document.{filed,reclassified}` AND
            # `capture.{filed,reclassified}` without per-kind branches.
            env_type = envelope.get("type", "")
            if env_type.endswith(".filed"):
                break
            if not env_type.endswith(".reclassified"):
                break
            grandparent_id = self._in_reply_to_id(parent)
            grandparent = await self._fetch_event(room_id, grandparent_id) if grandparent_id else None
            if grandparent is None:
                break
            prior = _strip_reply_fallback(getattr(grandparent, "body", "") or "")
            if prior:
                bodies.append(prior)
            parent_id = self._in_reply_to_id(grandparent)

        if not bodies:
            return ("", latest_state)
        if len(bodies) == 1:
            return (bodies[0], latest_state)
        numbered = "\n".join(f"  {i+1}. {b}" for i, b in enumerate(bodies))
        hint = (
            "Conversation of corrections, most recent first. The most "
            "recent line supersedes earlier ones when they conflict for "
            "the same field; merge non-conflicting fields:\n" + numbered
        )
        return (hint, latest_state)

    async def _collect_correction_hint(self, room_id: str, event) -> str:
        """Back-compat wrapper -- some callers (and older tests) just
        want the hint string. Internally delegates to the chain walker."""
        hint, _initial = await self._collect_correction_chain(room_id, event)
        return hint

    @staticmethod
    def _in_reply_to_id(event) -> str | None:
        """The event_id this event replies to, or None if it isn't a reply."""
        return (
            getattr(event, "source", {})
            .get("content", {})
            .get("m.relates_to", {})
            .get("m.in_reply_to", {})
            .get("event_id")
        )

    async def _fetch_event(self, room_id: str, event_id: str | None):
        """Best-effort `room_get_event`; returns the event or None on any failure."""
        if not event_id:
            return None
        try:
            resp = await self._client.room_get_event(room_id, event_id)
        except Exception as e:
            logger.debug("[archivist] event fetch failed for {}: {}", event_id, e)
            return None
        return getattr(resp, "event", None)

    async def _handle_reply_reprocess(
        self, room_id: str, doc_id: int, user_hint: str, reply_to: str,
        *, date_filed: str | None = None,
        initial_classification: dict | None = None,
    ) -> None:
        """Re-enrich `doc_id` with the user's reply as a correction hint.

        The pipeline does the work and returns a ReprocessOutcome; this
        maps it to a chat reply. The reclassified confirmation carries a
        fresh `document.reclassified` envelope so the user can chain
        another correction by replying to it.
        """
        o = await self._pipeline.reprocess(
            doc_id=doc_id, user_hint=user_hint, date_filed=date_filed,
            initial_classification=initial_classification,
        )
        if o.status == "doc_missing":
            await self._send(
                room_id, self.t("reprocess_doc_missing", doc_id=doc_id), reply_to,
            )
        elif o.status == "llm_error":
            kind, detail = o.llm_error
            await self._send(
                room_id,
                self.t("reprocess_llm_error", doc_id=doc_id, kind=kind, detail=detail),
                reply_to,
            )
        else:  # reclassified
            reply = render_reprocessed_reply(
                self.t,
                title=o.title,
                doc_id=doc_id,
                resolved_topics=o.resolved_topics,
                resolved_persons=o.resolved_persons,
                resolved_type=o.resolved_type,
                resolved_correspondent=o.resolved_correspondent,
            )
            await self._send(
                room_id, reply, reply_to,
                metadata={"dev.famstack.event": o.envelope},
            )

    async def _handle_reply_capture_reprocess(
        self, room_id: str, vault_path: str, user_hint: str,
        sender_mxid: str, reply_to: str,
        *, initial_classification: dict | None = None,
    ) -> None:
        """Re-classify a capture with the user's reply as a hint.

        Parallels `_handle_reply_reprocess` for documents: dispatch to
        the pipeline, render via the shared reprocessed presenter,
        attach the fresh `capture.reclassified` envelope so the user
        can chain another correction by replying to THIS message.
        """
        outcome = await self._capture.reprocess(
            vault_path=vault_path, user_hint=user_hint,
            sender_mxid=sender_mxid,
            initial_classification=initial_classification,
        )
        # `_reply_for_capture` already knows how to render the
        # reclassified branch + attach the envelope, so we just defer.
        await self._reply_for_capture(room_id, outcome, reply_to)

    async def _process_document(
        self, room_id: str, filename: str, display_name: str,
        file_data: bytes, reply_to: str | None = None,
        date_filed: str | None = None,
        submitter_mxid: str | None = None,
        user_hint: str | None = None,
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
            user_hint=user_hint,
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
            await self._answer(room_id, self.t("upload_failed", name=o.display_name), reply_to)
            return
        if o.status == "duplicate":
            await self._answer(room_id, self._duplicate_reply(o.display_name, o.duplicate), reply_to)
            return
        if o.status == "ocr_failed":
            await self._answer(room_id, self.t("ocr_failed", name=o.display_name), reply_to)
            return
        if o.status == "filed_no_details":
            await self._answer(
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
            await self._answer(room_id, self.t(key, **kwargs), reply_to)
        elif not o.has_text:
            await self._answer(
                room_id, self.t("filed_no_text", name=o.display_name, link=o.link),
                reply_to,
            )
        elif not o.classify_enabled:
            await self._answer(
                room_id, f"{self.t('filed', title=o.display_name)}\n\n  {o.link}",
                reply_to,
            )
        elif not o.classification:
            await self._answer(
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
            await self._answer(
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

        # Voice messages depend on the optional whisper service. When the
        # transcriber wasn't wired (WHISPER_URL unset or whisper missing
        # at boot), silently ignore audio so the bot still files PDFs and
        # images normally; the startup log already warned the admin.
        if msgtype == "m.audio" and self._transcriber is None:
            logger.debug(
                "[archivist] ignoring voice from {} (transcription disabled)",
                event.sender,
            )
            return

        url = content.get("url", "")
        if not url or not url.startswith("mxc://"):
            return

        ctx = self._room_context(room)
        # First-encounter welcome -- idempotent, gated by a per-room
        # state event. Runs ahead of the routing decision so the user
        # always sees the intro before any other reply from the bot.
        await self._send_room_welcome_if_needed(room, ctx)
        mentioned = self._is_bot_mentioned(event)
        if not await self._should_react(ctx, mentioned=mentioned):
            logger.debug(
                "[archivist] skipping file from {} in {} per room mode",
                event.sender, ctx.room_id,
            )
            return

        # On modern Matrix clients an upload can carry a human caption
        # alongside the file (Element X, FluffyChat, anything honoring
        # MSC4274). When present, the caption rides into the classify
        # prompt as user_hint -- "neue Personalausweise für Marge und
        # Bart" steers the LLM the same way a reply-to-correct does,
        # without the user having to wait for a bad classification first.
        raw_filename = content.get("filename") or content.get("body") or "document"
        caption = _attachment_caption(content)
        display_name = _clean_filename(raw_filename, msgtype)
        reply_to = event.event_id

        # Multi-page scan / multi-message batch mode. PDFs and images
        # take the existing page-accumulator path; voice memos take the
        # transcription-and-accumulate path so a single `(` ... `)`
        # session can collect either kind (or both, filed as separate
        # captures on close).
        if event.sender in self._scan_sessions:
            if msgtype == "m.audio":
                await self._handle_voice_batch_message(
                    room.room_id, event, url, raw_filename,
                )
            else:
                await self._handle_scan_page(
                    room.room_id, event, url, raw_filename, caption,
                )
            return

        file_data = await self._download_media(url)
        if not file_data:
            await self._send(room.room_id, self.t("download_failed_matrix", name=display_name), reply_to)
            return

        # Acknowledge the upload with a 👀 reaction on the source message
        # the moment work starts, instead of a "Received X, analyzing..."
        # reply. The reaction is attached to the message being processed
        # and adds no separate timeline event per capture; the final
        # filing reply (or an error reply) is the real closure signal.
        await self._react(room.room_id, reply_to, EYES)

        # Set typing after the ack so the indicator stays alive through
        # the OCR + classify + mirror work that follows. (The old code
        # had to send the "Received X" reply first because a chat message
        # clears the indicator; a reaction is the last send before this,
        # so typing set here survives.)
        await self._set_typing(room.room_id, on=True)

        # Documents room → full archivist pipeline (Paperless + classify
        # + entity reconciliation). Every other reacting room is a
        # capture room: PDFs and images become visual bookmarks in the
        # sender's bucket; Matrix already stores the binary, so we link
        # to the mxc URL and don't re-archive the bytes.
        #
        # Voice memos always take the capture path, even in #documents:
        # a transcript belongs in the sender's own notes, not in Paperless
        # alongside scanned invoices and IDs.
        if self._is_documents_room(ctx) and msgtype != "m.audio":
            await self._process_document(
                room.room_id, raw_filename, display_name, file_data, reply_to,
                date_filed=self._event_date(event),
                submitter_mxid=event.sender,
                user_hint=caption or None,
            )
        else:
            # A bot-posted email attachment (dev.famstack.attachment) is filed
            # on behalf of the source: don't attribute the bot as the person,
            # and carry email provenance tags. The bucket still comes from the
            # room's topic binding (e.g. a "Family Email" topic).
            attach = content.get(self.ATTACHMENT_KEY)
            bot_attachment = isinstance(attach, dict)
            extra_seed_topics = None
            if bot_attachment:
                extra_seed_topics = ["email"]
                frm = (attach.get("from") or "").strip()
                if frm:
                    extra_seed_topics.append(f"Sender: {frm}")
            await self._handle_binary_capture(
                room_id=room.room_id,
                file_data=file_data,
                mime=content.get("info", {}).get("mimetype")
                     or _guess_mime(raw_filename, msgtype),
                filename=raw_filename,
                source_uri=url,
                sender_mxid=event.sender,
                capture_id=event.event_id,
                reply_to=reply_to,
                default_person=not bot_attachment,
                extra_seed_topics=extra_seed_topics,
            )

    async def _on_text(self, room, event: RoomMessageText) -> None:
        if event.sender == self.user_id:
            return

        # Inbound source event: a `dev.famstack.source` block means another
        # bot (the mail bot today) already fetched external content and
        # posted it here. Fold it through the capture pipeline — one capture
        # path — then stop; it is not a user query or paste to route.
        source = event.source.get("content", {}).get(self.SOURCE_KEY)
        if isinstance(source, dict):
            await self._handle_source_message(room, event, source)
            return

        # Plain chatter from another bot (a join welcome, a status line) is
        # not a user capture or query — only its `dev.famstack.source` events
        # above are actionable. Ignoring it prevents bot-to-bot capture loops
        # (e.g. filing the mail bot's welcome as a note).
        if event.sender.split(":")[0].lstrip("@").endswith("-bot"):
            return

        query = event.body.strip()
        if not query:
            return
        # Room-config commands (`!config ...`) are handled before the
        # room-mode gate, so a room can always be switched back out of
        # react mode, and before routing so they never read as a capture.
        if await self._maybe_handle_config_command(room, event):
            return
        query_lower = query.lower()
        reply_to = event.event_id

        ctx = self._room_context(room)
        # First-encounter welcome -- idempotent, gated by a per-room
        # state event. Runs ahead of the routing decision so the user
        # always sees the intro before any other reply from the bot.
        await self._send_room_welcome_if_needed(room, ctx)
        mentioned = self._is_bot_mentioned(event)
        is_documents = self._is_documents_room(ctx)
        logger.info(
            "[archivist] text from {} in {} (alias={!r}, dm={}, docs={}, "
            "members={}, mention={})",
            event.sender, ctx.room_id, ctx.alias, ctx.is_dm,
            is_documents, len(ctx.members), mentioned,
        )
        if not await self._should_react(ctx, mentioned=mentioned):
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
            formatted_body = event.source.get("content", {}).get("formatted_body")
            query = self.strip_mention(
                query, self.user_id, formatted_body=formatted_body,
            )
            if not query:
                query = "help"
            query_lower = query.lower()

        # ── Reply-to-classification: user is correcting a prior filing ──
        # When the user replies to a bot's filing message WITHOUT
        # @-mentioning the bot, that's a correction: trace back the
        # target from the parent event's envelope, re-run the
        # classifier with the user's message as an authoritative
        # hint. An @-mention is a different intent -- the user is
        # addressing the bot conversationally (search, help), so we
        # skip the reprocess path and let the dispatcher route on
        # query content instead. Some clients (Element X) attach
        # `m.in_reply_to` to mentioned messages; without this guard
        # those searches would be eaten by the reprocess path.
        if not mentioned:
            doc_id = await self._reply_target_doc_id(room.room_id, event)
            if doc_id is not None:
                hint, initial = await self._collect_correction_chain(
                    room.room_id, event,
                )
                if hint:
                    await self._handle_reply_reprocess(
                        room.room_id, doc_id, hint, reply_to,
                        date_filed=self._event_date(event),
                        initial_classification=initial,
                    )
                    return

            # Same shape for captures: a reply to a `capture.filed` or
            # `capture.reclassified` confirmation reaches the capture
            # pipeline's reprocess. The chain walker is generic over
            # filing kind, so it Just Works for either side.
            capture_path = await self._reply_target_capture_path(
                room.room_id, event,
            )
            if capture_path is not None:
                hint, initial = await self._collect_correction_chain(
                    room.room_id, event,
                )
                if hint:
                    await self._handle_reply_capture_reprocess(
                        room.room_id, capture_path, hint, event.sender, reply_to,
                        initial_classification=initial,
                    )
                    return

        if query_lower in HELP_COMMANDS:
            # Same per-room welcome the bot posted on first encounter
            # -- the user re-reads exactly what fits where they asked.
            await self._send(
                room.room_id,
                self._welcome_text_for(room, ctx),
                reply_to,
            )

        # `(` and `scan` open a multi-page session and accept an
        # optional caption inline: `( neue Personalausweise`. The
        # caption rides through to classify alongside any per-page
        # captions and the closer's trailing text.
        elif (begin := _split_scan_command(query, SCAN_BEGIN))[0]:
            sender_name = event.sender.split(":")[0].replace("@", "").capitalize()
            self._scan_sessions[event.sender] = {
                "files": [], "voice_inputs": [],
                "room_id": room.room_id, "caption": begin[1],
            }
            await self._send(room.room_id, self.t("scan_started", sender=sender_name), reply_to)

        elif (end := _split_scan_command(query, SCAN_END))[0]:
            if event.sender in self._scan_sessions:
                if end[1]:
                    session = self._scan_sessions[event.sender]
                    session["caption"] = _join_captions(
                        session.get("caption", ""), end[1],
                    )
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
                    capture_id=event.event_id,
                )

        elif not mentioned and (embedded_url := _first_url(query)) is not None:
            # Chat-shaped message with a URL embedded ("Interesting
            # facts: <url>", "look at this gear list <url>"). The URL
            # is the payload; the surrounding text is framing the user
            # wrote. Capture the URL, pass the framing as user_hint so
            # the classifier's title and summary reflect what the user
            # actually said about it. @-mentioned messages still flow
            # to search -- there a URL is conversational, not a drop.
            hint = query.replace(embedded_url, "", 1).strip(
                " \t\n\r:.,;!?—-",
            )
            if is_documents:
                await self._handle_url(
                    room.room_id, embedded_url, reply_to,
                    date_filed=self._event_date(event),
                    submitter_mxid=event.sender,
                )
            else:
                await self._handle_capture(
                    room.room_id, embedded_url, event.sender, reply_to,
                    capture_id=event.event_id,
                    user_hint=hint or None,
                )

        elif mentioned or is_documents:
            # Free-text search runs whenever the user explicitly
            # addressed the bot (any room) or the message landed in
            # the documents room (where search is the default). A
            # short chat-shaped query like "Duff Insurance" is fine — that's
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
                capture_id=event.event_id,
            )

        else:
            # Short message in a capture room — ignored. Pasting more
            # context will trigger capture; chat-shaped messages don't.
            logger.debug(
                "[archivist] capture room {} ignored short text: {!r}",
                room.room_id, query[:60],
            )

    # ── Reactions: user → bot per-message routing ────────────────────────
    #
    # A small hard-coded registry maps a (normalized) emoji to the handler
    # method that runs when a family member drops it on a message. To add a
    # binding — 🗑 redact, 👎 reclassify — add one entry here and write the
    # `_react_*` method; the dispatcher needs no changes. Handlers receive
    # the already-fetched target event, so they decide for themselves
    # whether a bot-authored target is valid (bookmark says no, redact yes).

    def _reaction_handlers(self) -> dict:
        return {
            "🔖": self._react_bookmark,
            "📌": self._react_bookmark,
        }

    async def _on_reaction(self, room, event) -> None:
        """Dispatch a user's reaction to its registered handler.

        Generic and binding-agnostic: ignore the bot's own (and other
        bots') reactions, normalize the emoji, look up the handler, fetch
        the reacted message once, and hand it off. Idempotency and any
        bot-target policy live in the handlers, keyed on the target event
        id so a drain replay dedups rather than acting twice.
        """
        if event.sender == self.user_id or self.is_bot_user(event.sender):
            return
        emoji = self.normalize_emoji(getattr(event, "key", ""))
        handler = self._reaction_handlers().get(emoji)
        if handler is None:
            return
        target_id = getattr(event, "reacts_to", None)
        if not target_id:
            return
        try:
            resp = await self._client.room_get_event(room.room_id, target_id)
        except Exception as e:
            logger.debug("[archivist] reaction target fetch failed: {}", e)
            return
        target = getattr(resp, "event", None)
        if target is None:
            return
        await handler(room, event, target, target_id)

    async def _react_bookmark(self, room, event, target, target_id) -> None:
        """🔖 / 📌 — bookmark the reacted message into the room: the same
        capture auto-mode would make, but on demand. The only capture path
        in a `!config process react` room.

        Never bookmarks a bot message (a filing, a welcome). Attribution
        is the message author, not the reactor — we're saving their
        content — and the capture is keyed on the target event id so a
        replay or a second reactor dedups downstream.
        """
        if self.is_bot_user(getattr(target, "sender", "")):
            return
        body = (getattr(target, "body", "") or "").strip()
        if not body:
            # v1 bookmarks text/URL messages; file uploads are a follow-up.
            return
        author = target.sender
        if _is_just_url(body):
            await self._handle_capture(
                room.room_id, body, author, target_id, capture_id=target_id,
            )
        elif (embedded_url := _first_url(body)) is not None:
            hint = body.replace(embedded_url, "", 1).strip(" \t\n\r:.,;!?—-")
            await self._handle_capture(
                room.room_id, embedded_url, author, target_id,
                capture_id=target_id, user_hint=hint or None,
            )
        else:
            await self._handle_text_capture(
                room.room_id, body, author, target_id, capture_id=target_id,
            )

    # ── Inbound source events (mail bot, future ingest channels) ──────────

    async def _handle_source_message(self, room, event, source: dict) -> None:
        """Fold a source-bot ingest message (mail bot today) into the vault.

        The mail bot posts a `dev.famstack.source` message carrying the raw
        original (see MicroBot.post_source_message); the archivist files it
        through the same CapturePipeline a pasted URL uses — one capture
        path, no duplicated pipeline. Only `source == "email"` is handled
        today; other kinds are ignored until their consumer lands.

        Trust boundary (revisited in the email security review): only bot
        senders may emit source events, so a family member cannot spoof an
        ingest. The raw email is untrusted *data*, never instructions — the
        classifier must treat it as content to summarise, not commands to
        obey.
        """
        if source.get("source") != "email":
            return
        sender_local = event.sender.split(":")[0].lstrip("@")
        if not sender_local.endswith("-bot"):
            logger.warning(
                "[archivist] ignoring dev.famstack.source from non-bot {}",
                event.sender,
            )
            return
        if self._capture is None:
            return
        raw = source.get("raw_content") or ""
        if not raw.strip():
            return
        # Defang links before the body is stored/rendered so a phishing URL
        # in the vault entry is plain, non-clickable text. The faithful
        # version stays on the source event's raw_content (reproducibility).
        body = defang_links(raw)

        # Provenance tags: which mailbox + folder this arrived in, so the
        # vault can filter "all work mail" / "everything in Schule". Same
        # "Prefix: Value" shape the vault already uses for "Person: X".
        seed_topics: list[str] = []
        if source.get("from"):
            seed_topics.append(f"Sender: {source['from']}")
        if source.get("account"):
            seed_topics.append(f"Mailbox: {source['account']}")
        if source.get("folder"):
            seed_topics.append(f"Folder: {source['folder']}")

        # Scope-aware placement: email inherits the bucket of the room it
        # lands in, then nests under emails/ (kind="email"). The family room
        # has no binding -> shared_bucket -> family/emails/; a topic room
        # ("Family E-Mails", "Hobby") routes to its bucket so a mailbox's mail
        # is filed by the scope of its room, co-located with that room's
        # attachments. The topic tag rides along as a seed.
        binding = await self._topic_binding(room, event.sender)
        if binding:
            bucket = binding.bucket
            if binding.seed_topics:
                seed_topics.extend(binding.seed_topics)
        else:
            # No topic: scope by room membership (a DM/private room → its
            # human; the shared family room → the shared bucket).
            bucket = self._scope_bucket(room)
        outcome = await self._capture.capture_email(
            subject=source.get("subject"),
            body=body,
            message_id=source.get("message_id"),
            thread_root=source.get("thread_root"),
            sender_mxid=event.sender,
            from_addr=source.get("from"),
            captured_at=source.get("captured_at"),
            bucket=bucket,
            seed_topics=seed_topics or None,
        )
        logger.info(
            "[archivist] email folded ({}) -> {}",
            outcome.status, outcome.vault_path,
        )

        # Drop the filing envelope onto the timeline (a reply to the email)
        # so the deriver and reprocess have the ledger event, exactly as a
        # paste capture does.
        if outcome.envelope:
            title = (outcome.classification or {}).get("title") or "Email"
            await self._send(
                room.room_id,
                f"Filed email: {title}",
                reply_to=event.event_id,
                metadata={self.FAMSTACK_EVENT_KEY: outcome.envelope},
            )

    # ── Scan mode ────────────────────────────────────────────────────────

    async def _handle_scan_page(
        self, room_id: str, event, url: str, raw_filename: str,
        caption: str = "",
    ):
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
        if caption:
            session["caption"] = _join_captions(session.get("caption", ""), caption)
        # 👀 on the page instead of a "Page N received." line — the
        # batch can run several pages deep, so a reaction per page keeps
        # the timeline clean; the scan-complete reply is the closure.
        await self._react(room_id, reply_to, EYES)

    async def _handle_voice_batch_message(
        self, room_id: str, event, url: str, raw_filename: str,
    ):
        """Transcribe a voice memo and stash it on the open batch session.

        Mirrors _handle_scan_page: download, normalise input, append to
        the session, ack the count. The transcript is computed eagerly
        (with the LLM cleanup pass) so by the time `)` arrives we just
        concatenate ready text -- no extra round-trip on close.
        """
        reply_to = event.event_id
        if self._transcriber is None:
            await self._send(
                room_id, self.t("scan_voice_no_transcriber"), reply_to,
            )
            return
        try:
            audio = await self._download_media(url)
        except Exception as e:
            await self._send(
                room_id, self.t("scan_voice_failed", error=str(e)), reply_to,
            )
            return
        if not audio:
            await self._send(
                room_id, self.t("scan_voice_failed_matrix"), reply_to,
            )
            return

        cleanup_llm = self._classifier.llm if self._classifier is not None else None
        try:
            transcript = await self._transcriber.transcribe(
                audio, filename=raw_filename or "voice.ogg",
                cleanup_with=cleanup_llm,
            )
        except LLMError as e:
            logger.warning("[archivist] batch voice transcribe failed: {}", e)
            await self._send(
                room_id, self.t("scan_voice_failed", error=str(e)), reply_to,
            )
            return

        if not transcript.strip():
            await self._send(room_id, self.t("scan_voice_empty"), reply_to)
            return

        session = self._scan_sessions[event.sender]
        session["voice_inputs"].append({
            "transcript": transcript,
            "mxc": url,
            "event_id": event.event_id,
        })
        # 👀 acknowledges the memo landed in the batch; the scan-complete
        # reply is the closure (mirrors _handle_scan_page).
        await self._react(room_id, reply_to, EYES)

    async def _handle_scan_complete(
        self, room_id: str, sender: str, reply_to: str | None = None,
        *, date_filed: str | None = None,
    ):
        session = self._scan_sessions.pop(sender)
        files = session["files"]
        voice_inputs = session.get("voice_inputs") or []
        caption = session.get("caption", "").strip()
        sender_name = sender.split(":")[0].replace("@", "").capitalize()

        # Nothing accumulated -- the user opened a session and closed it
        # without sending anything. Treat as cancelled, same as today.
        if not files and not voice_inputs:
            await self._send(room_id, self.t("scan_cancelled"), reply_to)
            return

        # Files (PDFs / images) take the existing PDF combine path. A
        # mixed batch files both: the PDF as a document and the voice
        # memos as a separate note below, since the vault data model
        # has one body per capture.
        if files:
            if len(files) == 1:
                filename, file_data = files[0]
                display_name = _clean_filename(filename)
                await self._send(
                    room_id, self.t("scan_complete_single"), reply_to,
                )
                await self._process_document(
                    room_id, filename, display_name, file_data, reply_to,
                    date_filed=date_filed,
                    submitter_mxid=sender,
                    user_hint=caption or None,
                )
            else:
                page_count = len(files)
                await self._send(
                    room_id,
                    self.t("scan_complete_multi", count=page_count),
                    reply_to,
                )
                try:
                    pdf_data = _combine_images_to_pdf(files)
                except Exception as e:
                    await self._send(
                        room_id,
                        self.t("scan_combine_failed", error=str(e)),
                        reply_to,
                    )
                    return
                filename = f"scan-{sender_name.lower()}-{page_count}p.pdf"
                display_name = f"scan ({page_count} pages)"
                await self._process_document(
                    room_id, filename, display_name, pdf_data, reply_to,
                    date_filed=date_filed,
                    user_hint=caption or None,
                    submitter_mxid=sender,
                )

        if voice_inputs:
            await self._handle_voice_batch_complete(
                room_id, sender, voice_inputs, reply_to,
            )

    async def _handle_voice_batch_complete(
        self, room_id: str, sender: str, voice_inputs: list[dict],
        reply_to: str | None,
    ):
        """File the accumulated voice memos as one combined note.

        Each input already carries a cleaned transcript (the LLM pass
        ran at message time), so this just hands the list to the
        capture pipeline and renders the resulting reply. We post a
        progress line first because the classify call is the slowest
        step and the sender deserves to know we're working.
        """
        n = len(voice_inputs)
        await self._send(
            room_id, self.t("scan_voice_complete", count=n), reply_to,
        )
        binding = await self._topic_binding(
            self._room_by_id(room_id), sender,
        )
        outcome = await self._capture.capture_voice_batch(
            transcripts=[v["transcript"] for v in voice_inputs],
            primary_mxc=voice_inputs[0].get("mxc"),
            sender_mxid=sender,
            capture_id=voice_inputs[0].get("event_id"),
            seed_topics=binding.seed_topics if binding else None,
            bucket=binding.bucket if binding else None,
        )
        await self._reply_for_capture(room_id, outcome, reply_to)

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
        """A Notifier bound to this room + reply thread for mid-flow status.

        Carries a 👀 react thunk so the capture pipeline can acknowledge
        the source message instead of posting a "Reading ..." status line."""
        async def react(rid: str, eid: str) -> None:
            await self._react(rid, eid, EYES)

        return MatrixNotifier(
            room_id=room_id, reply_to=reply_to, send=self._send, t=self.t,
            react=react,
        )

    async def _handle_capture(
        self, room_id: str, url: str, sender_mxid: str,
        reply_to: str | None = None, *, capture_id: str | None = None,
        user_hint: str | None = None,
    ) -> None:
        binding = await self._topic_binding(
            self._room_by_id(room_id), sender_mxid,
        )
        outcome = await self._capture.capture_url(
            url=url, sender_mxid=sender_mxid,
            notifier=self._notifier(room_id, reply_to),
            capture_id=capture_id,
            seed_topics=binding.seed_topics if binding else None,
            bucket=binding.bucket if binding else None,
            user_hint=user_hint,
        )
        await self._reply_for_capture(room_id, outcome, reply_to)

    async def _handle_text_capture(
        self, room_id: str, text: str, sender_mxid: str,
        reply_to: str | None = None, *, capture_id: str | None = None,
    ) -> None:
        binding = await self._topic_binding(
            self._room_by_id(room_id), sender_mxid,
        )
        outcome = await self._capture.capture_text(
            text=text, sender_mxid=sender_mxid,
            capture_id=capture_id,
            seed_topics=binding.seed_topics if binding else None,
            bucket=binding.bucket if binding else None,
        )
        await self._reply_for_capture(room_id, outcome, reply_to)

    async def _handle_binary_capture(
        self, *, room_id: str, file_data: bytes, mime: str,
        filename: str, source_uri: str, sender_mxid: str,
        capture_id: str | None = None,
        reply_to: str | None = None,
        default_person: bool = True,
        extra_seed_topics: list[str] | None = None,
    ) -> None:
        """Capture a PDF or image as a visual bookmark.

        Vision-driven by default (the classifier sees the rendered
        page(s) + any extractable text layer). The mirror entry
        points back at the Matrix mxc URL -- the binary stays where
        the homeserver put it, the wiki just summarises and links.
        ``capture_id`` is the Matrix event_id of the upload, stored
        on the entry as a stable correlation key so a deriver (or
        the reply-to-correct path) can find this capture later
        without depending on the title-derived path.

        ``default_person`` is False for bot-posted content (an email
        attachment): the sender is a bot, not the owner, so don't fall
        it in as the person. ``extra_seed_topics`` prepend provenance
        tags (Sender, ``email``) on top of any topic-room seeds.
        """
        room = self._room_by_id(room_id)
        binding = await self._topic_binding(room, sender_mxid)
        seed_topics = list(extra_seed_topics or [])
        if binding:
            bucket = binding.bucket
            if binding.seed_topics:
                seed_topics.extend(binding.seed_topics)
        elif not default_person:
            # Bot-posted (email attachment) with no topic: scope by membership
            # so a DM/private room files under its human, not @mail-bot. Human
            # uploads keep bucket=None -> the sender-bucket fallback.
            bucket = self._scope_bucket(room)
        else:
            bucket = None
        outcome = await self._capture.capture_binary(
            file_data=file_data, mime=mime, filename=filename,
            source_uri=source_uri, sender_mxid=sender_mxid,
            capture_id=capture_id,
            seed_topics=seed_topics or None,
            bucket=bucket,
            default_person=default_person,
        )
        await self._reply_for_capture(room_id, outcome, reply_to)

    async def _reply_for_capture(
        self, room_id: str, o: CaptureOutcome, reply_to: str | None,
    ) -> None:
        """Map a CaptureOutcome to a chat reply.

        `empty` (whitespace-only paste) is a silent drop; terminal
        statuses get a one-line message; a capture or a reclassified
        capture renders via the shared presenter and carries the
        `capture.*` envelope as metadata so the user can reply-to-
        correct the same way they do with document filings.
        """
        if o.status == "empty":
            return
        if o.status == "extract_failed":
            # The reason qualifies what failed so the family doesn't hear
            # "couldn't read that link" after sending a voice memo. Falls
            # back to the original URL-shaped message for legacy callers.
            failure_keys = {
                "url": "capture_failed",
                "transcription": "capture_failed_transcription",
                "binary": "capture_failed_binary",
            }
            key = failure_keys.get(o.failure_reason or "", "capture_failed")
            await self._answer(room_id, self.t(key), reply_to)
            return
        if o.status == "no_mirror":
            await self._answer(room_id, self.t("capture_no_mirror"), reply_to)
            return
        if o.status == "reclassified":
            reply = render_reprocessed_reply(
                self.t,
                title=o.classification.get("title") or o.source_title_hint or "",
                doc_id=None,
                resolved_topics=[t for t in (o.classification.get("tags") or [])
                                 if isinstance(t, str)],
                resolved_persons=[p for p in (o.classification.get("persons") or [])
                                  if isinstance(p, str)],
                resolved_type=None,
                resolved_correspondent=None,
            )
        else:
            reply = render_capture_reply(
                self.t,
                source_title_hint=o.source_title_hint,
                classification=o.classification,
                link=o.display_link,
                transcript=o.transcript,
            )
        metadata = (
            {"dev.famstack.event": o.envelope} if o.envelope else None
        )
        await self._answer(room_id, reply, reply_to, metadata=metadata)

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

        A search asked in a topic room auto-scopes to that topic's
        bucket -- the room context narrows the haystack. The user can
        still reach a wider search by asking outside the topic room.
        """
        await self._set_typing(room_id, on=True)

        topic_bucket: str | None = None
        if sender:
            binding = await self._topic_binding(
                self._room_by_id(room_id), sender,
            )
            if binding is not None:
                topic_bucket = binding.bucket

        reply = await self._search.run(
            query=query, sender=sender,
            notifier=self._notifier(room_id, reply_to),
            topic_bucket=topic_bucket,
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
