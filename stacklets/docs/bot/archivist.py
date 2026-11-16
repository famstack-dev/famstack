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
from dataclasses import dataclass
from pathlib import Path

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

from .capture_tags import CaptureTagCache
from .extractors import TextExtractor, UrlExtractor
from .git_mirror import GitMirror
from .pdf_analysis import (
    DEFAULT_REFORMAT_MAX_PDF_PAGES,
    DEFAULT_VISION_MAX_PDF_PAGES,
)
from .pipeline import (
    Classifier,
    DEFAULT_CLASSIFY_MAX_CHARS,
    PaperlessAPI,
)
from stack import resolve_model
from stack.bot import CHECK, CROSS, EYES, MicroBot, RoomContext, normalize_alias, voice
from stack.email_message import defang_links
from stack.ai.client import (
    ModelCapabilities,
)
from stack.cards.sources import time_of_ms

from .archivist_captures import NO_MENTIONS, ArchivistCaptures, card_type, guess_mime, shown_in_chat
from .archivist_documents import SUPPORTED_MSGTYPES, ArchivistDocuments, event_date
from .archivist_rooms import ArchivistRooms
from .archivist_texts import HELP_COMMANDS, SCAN_BEGIN, SCAN_END, language_option, t
from .text_utils import (
    attachment_caption as _attachment_caption,
    clean_filename as _clean_filename,
    first_url as _first_url,
    is_just_url as _is_just_url,
    join_captions as _join_captions,
    looks_like_paste,
    split_scan_command as _split_scan_command,
    strip_reply_fallback as _strip_reply_fallback,
)
from .diary_room import DEFAULT_JOB_AT, DiaryRoom
from .reply_presenter import (
    render_capture_reply,
    render_reprocessed_reply,
)
from .document_pipeline import (
    DocumentPipeline,
)
from .reasons import why
from .search_service import SearchService
from .source_archive import SourceAttachment, build_source_pdf
from .capture_pipeline import CapturePipeline
from .vault_context import VaultContext


# ── Constants ────────────────────────────────────────────────────────────────

# The provenance tags the archivist puts on a filed email: who sent it,
# and the mailbox and folder it came from. The card keeps them for
# filtering; a capture's reply does not show them, because the email
# right above it in the thread says the same. The card stores them slugged
# (`mailbox-family`, `EMAIL_PROVENANCE_SLUGS`), so a correction reads them
# back in that form.
EMAIL_SENDER_TAG = "Sender: "
EMAIL_MAILBOX_TAG = "Mailbox: "
EMAIL_FOLDER_TAG = "Folder: "


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

@dataclass(frozen=True)
class ArchivistServices:
    """Everything the archivist delegates work to.

    Built once by `ArchivistBot.start()`, or handed to the constructor by
    a caller that never starts the bot (a test driving one handler).
    `classifier` is None without an AI endpoint and `mirror` is None
    without Forgejo; every other service is always present.
    """

    paperless: PaperlessAPI
    classifier: Classifier | None
    url_extractor: UrlExtractor
    text_extractor: TextExtractor
    capture_tags: CaptureTagCache
    mirror: GitMirror | None
    vault: VaultContext
    pipeline: DocumentPipeline
    search: SearchService
    capture: CapturePipeline


class ArchivistBot(MicroBot):
    """Document filing bot — watches a Matrix room for uploads, classifies
    them with an LLM, and files them in Paperless-ngx."""

    name = "archivist-bot"

    # Assigned by the constructor or by start(), never None. Reading it
    # before either ran is a bug, and an AttributeError here names it
    # where a None would travel on into a handler.
    _services: ArchivistServices

    # Filing is the slowest thing any famstack bot does, and the framework
    # default (180s) is a chat-bot budget, not a document-pipeline one. One
    # archived document costs, in sequence: the Paperless upload + OCR wait
    # (up to 120s), a vision classify pass, a layout reformat pass, and the
    # entity-enrichment pass — three separate LLM calls, each paying its own
    # prefill before it emits a token.
    #
    # Those prefills are the whole budget. Measured on a dense 14-page PDF
    # against local oMLX, one 28K-token pass took 163s on a machine with RAM
    # to spare; a 16GB Mac running photos and documents alongside is several
    # times slower. Three such calls plus the OCR wait clears eight minutes
    # comfortably, which is why 480s still cancelled long documents and threw
    # the work away.
    #
    # 30 minutes is deliberately far past any healthy filing. It is a
    # stuck-handler guard, not a latency target: the pipeline's own per-step
    # timeouts bound normal slowness, and a handler that does blow this now
    # fails visibly (❌ + a threaded notice) and can be retried with 🔁.
    HANDLER_TIMEOUT_SECONDS = 1800

    def __init__(
        self, homeserver, user_id, password, session_dir, *,
        services: ArchivistServices | None = None, **settings,
    ):
        super().__init__(homeserver, user_id, password, session_dir, **settings)
        if services is not None:
            self._services = services
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
        # Links to a *document* are the exception and go one better:
        # they are built from `link_base_url` below, so what lands in
        # chat is a logical `/go/docs/<id>` that re-resolves at click
        # time instead of a Paperless URL that ages badly.
        self.paperless_url = os.environ.get("PAPERLESS_URL", "")
        self.paperless_token = os.environ.get("PAPERLESS_TOKEN", "")
        self.paperless_public_url = os.environ.get("PAPERLESS_PUBLIC_URL", "")
        self.code_url = os.environ.get("CODE_URL", "")
        self.code_public_url = os.environ.get("CODE_PUBLIC_URL", "")
        # Base for every persistent `/go` link the bot posts into chat or
        # into an event envelope -- filing replies, search hits, todo
        # pages. Public/home base, mode-correct; `stack.links` joins the
        # logical path onto it. Empty when core hasn't rendered it yet ->
        # the link is simply omitted and the unlinked view is used.
        self.link_base_url = os.environ.get("LINK_BASE_URL", "")
        self.openai_url = os.environ.get("OPENAI_URL", "")
        # The stack runs without an AI for now (`[ai] provider = "none"`).
        self.ai_off = os.environ.get("AI_OFF") == "true"
        self.openai_key = os.environ.get("OPENAI_KEY", "")
        self.language = os.environ.get("LANGUAGE", "en")
        # `!config language`: the language a room's filings are written in.
        # Built here because its description names the household language.
        self.CONFIG_OPTIONS = {
            **MicroBot.CONFIG_OPTIONS,
            "language": language_option(self.language),
        }
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
        # The memories room is the family's own. The archivist posts the
        # diary's cards there once a day and takes corrections in their
        # threads, and says nothing else (see diary_room.py).
        self._diary = DiaryRoom(
            self,
            alias=normalize_alias(settings.get("memories_room_alias", "memories")),
            job_at=str(settings.get("diary_job_at", DEFAULT_JOB_AT)),
            state_path=Path(self._session_dir) / "diary-room.json",
        )
        self._diary_task: asyncio.Task | None = None
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
        self._scan_sessions: dict[str, dict] = {}
        # One collaborator per topic, each holding its own state. They get
        # this bot as their chat port (send, react, the client, `t`) and
        # their siblings explicitly, never this module.
        self._rooms = ArchivistRooms(
            self, diary=self._diary, services=lambda: getattr(self, "_services", None),
        )
        self._documents = ArchivistDocuments(
            self, rooms=self._rooms, services=lambda: self._services,
        )
        self._captures = ArchivistCaptures(
            self, rooms=self._rooms, services=lambda: self._services,
        )
        self._paperless_version: str = ""

    def t(self, key: str, **kwargs) -> str:
        return t(self.language, key, **kwargs)

    def register_callbacks(self, client: AsyncClient) -> None:
        self.add_event_callback(
            self._on_file,
            (RoomMessageMedia, RoomMessageImage, RoomMessageFile, RoomMessageAudio),
        )
        self.add_event_callback(self._on_text, RoomMessageText)
        # User → bot reactions: 🔖 to save the reacted message. The drain
        # delivers reactions as typed ReactionEvents.
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

        if not hasattr(self, "_services"):
            self._services = self._build_services()
        # Warm the vision-capability cache on every boot. Previously
        # this was kicked from on_first_sync, but MicroBot only runs
        # that hook once across the lifetime of the welcome marker,
        # so a restart never re-probed. Probe results are cached to
        # disk inside Classifier, so this is a no-op if the cache
        # is already populated.
        classifier = self._services.classifier
        if self.classify_enabled and classifier is not None:
            asyncio.create_task(classifier.has_vision())
        # The diary's daily job, on every boot for the same reason: the
        # first-sync hook runs once per install, not once per start.
        if self._diary_task is None:
            self._diary_task = asyncio.create_task(self._diary.loop())
        # The framework's start() owns the session loop and closes the
        # http session (via _aclose) on shutdown.
        await super().start()

    def _build_services(self) -> ArchivistServices:
        """Wire every service from the bot's config.

        Runs inside the event loop, before super().start() blocks in the
        sync loop, because the HTTP session has to be created there.
        """
        # Reuse the framework-owned aiohttp session — one pool, closed by
        # MicroBot on shutdown. Paperless / OpenAI / extractors share it.
        http = self._ensure_http()
        paperless = PaperlessAPI(http, self.paperless_url, self.paperless_token)
        # Vision-capability cache lives in the bot's data dir so a probe
        # done in one container restart isn't repeated by the next one.
        # When OPENAI_URL is empty, leave the classifier unconfigured —
        # the bot still does filing / search / URL archiving, and the
        # framework refuses to construct against a missing endpoint so
        # we don't silently leak documents to api.openai.com.
        classifier: Classifier | None = None
        if self.openai_url:
            classifier = Classifier.from_endpoint(
                self.openai_url, self.openai_key, bot_name=self.name,
                language=self.language,
                capabilities=ModelCapabilities(
                    path=self._session_dir / "model-capabilities.json",
                ),
            )
        elif self.classify_enabled:
            logger.warning(
                "[archivist] classify=true but OPENAI_URL is empty, "
                "so classification is off. Set up AI with './stack up ai'"
            )
            self.classify_enabled = False
            self.reformat_enabled = False
        url_extractor = UrlExtractor()
        text_extractor = TextExtractor()
        capture_tags = CaptureTagCache(
            self._session_dir / "capture-tags.json",
        )
        capture_tags.load()
        logger.info(
            "[archivist] capture tag cache: {} tags (keep_body={})",
            len(capture_tags.top(10_000)),
            self.capture_keep_body,
        )
        mirror = self._build_mirror()
        vault = VaultContext(language=self.language, shared_bucket=self.shared_bucket)
        mirror.vault = vault
        pipeline = DocumentPipeline(
            paperless=paperless,
            classifier=classifier,
            mirror=mirror,
            bot_name=self.name,
            language=self.language,
            classify_enabled=self.classify_enabled,
            reformat_enabled=self.reformat_enabled,
            classify_max_chars=self.classify_max_chars,
            vision_max_pdf_pages=self.vision_max_pdf_pages,
            reformat_max_pdf_pages=self.reformat_max_pdf_pages,
            paperless_public_url=self.paperless_public_url,
            link_base_url=self.link_base_url,
            actor=self.user_id,
            vault=vault,
        )
        search = SearchService(
            classifier=classifier,
            paperless=paperless,
            t=self.t,
            language=self.language,
            code_public_url=self.code_public_url,
            mirror_org=self.mirror_org,
            link_base_url=self.link_base_url,
            shared_bucket=self.shared_bucket,
            vault=vault,
        )
        capture = CapturePipeline(
            url_extractor=url_extractor,
            text_extractor=text_extractor,
            classifier=classifier,
            mirror=mirror,
            capture_tags=capture_tags,
            paperless=paperless,
            bot_name=self.name,
            classify_max_chars=self.classify_max_chars,
            capture_keep_body=self.capture_keep_body,
            capture_tag_prompt_size=self.capture_tag_prompt_size,
            vision_max_pdf_pages=self.vision_max_pdf_pages,
            llm=classifier.llm if classifier is not None else None,
            vault=vault,
        )
        return ArchivistServices(
            paperless=paperless,
            classifier=classifier,
            url_extractor=url_extractor,
            text_extractor=text_extractor,
            capture_tags=capture_tags,
            mirror=mirror,
            vault=vault,
            pipeline=pipeline,
            search=search,
            capture=capture,
        )

    def _build_mirror(self) -> GitMirror:
        """The archivist's writer of the family memory, through its curator,
        which answers each filing (`stack.memory`)."""
        # `self._session_dir` is already the in-container path the bot
        # runner mounts (`/data/<stacklet>/bot`). Don't read DATA_DIR —
        # that env var carries the host path and would dump mirror state
        # outside the container's volume mount.
        mirror = GitMirror(data_dir=self._session_dir, shared_bucket=self.shared_bucket)
        logger.info("[archivist] Memory vault writer: through the curator")
        return mirror





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
        ``ArchivistRooms.welcome_if_needed``.
        """

        for room_id in list(self.client.rooms):
            room = self.client.rooms[room_id]
            ctx = self._room_context(room)
            try:
                await self._rooms.welcome_if_needed(room, ctx)
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

        room = self._rooms.room_by_id(room_id)
        if room is None:
            return
        ctx = self._room_context(room)
        try:
            await self._rooms.welcome_if_needed(room, ctx)
        except Exception as e:
            logger.warning(
                "[archivist] welcome on join failed for {}: {}", room_id, e,
            )


    def _wants_voice(self, room, event) -> bool:
        """In the memories room, only a voice reply in a thread is ours to
        hear: it may be a correction to a card. A memo is the family's,
        and the diary transcribes it on its own schedule."""
        if self._diary.is_room(self._room_context(room)):
            return self.get_thread_root(event) is not None
        return True


















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
    def _is_filing_envelope(envelope: dict) -> bool:
        """Whether an envelope announces a filed item the user can correct.

        Any `*.filed` (the original) or `*.reclassified` (the result of
        an earlier correction round), generic over filing kind so
        documents and captures share one reading. Accepting the
        reclassified form is what lets a user chain corrections: the
        target of the next one is the most recent classification, not
        just the original filing.
        """
        return envelope.get("type", "").endswith((".filed", ".reclassified"))

    async def _thread_is_ours(self, room_id: str, event) -> bool:
        """Whether `event` is ours to act on, as far as threads decide it.

        A thread is a bounded conversation with an owner, so the
        archivist acts inside one only when the thread is its own -- the
        filing thread hanging off an upload, where "this is Marge's, not
        Homer's" is a correction. Every other thread is someone else's
        conversation and is left alone, including a thread no bot has
        answered in: two people talking to each other is not material
        dropped for filing.

        The main timeline is unchanged. A message that is in no thread is
        always ours to route -- that is where dropping something for the
        archivist happens.

        This is the gate that was missing. The family agent lives in the
        same rooms and answers in threads, and a person mid-conversation
        with it does not repeat its name on every line, so from here
        those lines looked like free-typed material: an agent error
        message became a note titled "Fehler beim Speichern der
        Packliste", and every reply typed afterwards read as a correction
        to that note, because the thread now held one of our filing
        cards.

        An @-mention skips this gate at the call site: deliberate address
        beats ambient context, the same rule corrections already follow.
        """
        # A handoff is addressed to us by contract, so no ambient rule
        # gets a say. The mail bot posts an email's attachments into the
        # thread under its own card, seconds before our filing lands
        # there, so at that moment the thread is still nobody's -- and
        # without this the school permission slip is silently dropped.
        if self.is_handoff(event):
            return True
        root = self.get_thread_root(event)
        if root is None:
            return True
        owner = await self.thread_owner(room_id, root)
        if owner == self.user_id:
            return True
        logger.info(
            "[archivist] thread {} belongs to {}; ignoring {} in {}",
            root, owner or "nobody", event.sender, room_id,
        )
        return False

    async def _correction_anchor(
        self, room_id: str, event,
    ) -> tuple[str, dict] | None:
        """The filing `event` is a correction to, as `(event_id, envelope)`.

        A correction is aimed at an *item*, not at a Matrix event, and
        the family member's client decides how to express "about this
        one". Three shapes reach the same filing, tried in the order
        that respects the user's most explicit gesture first:

        1. They quoted one of our classification messages: the reply
           parent carries the envelope, so take it. Deliberate, and the
           only shape available in a room without threads.
        2. They typed inside the filing's thread. The thread relation
           is the deliberate part; the `m.in_reply_to` a client folds
           in alongside it points at the newest event in the thread
           (see `_thread_envelopes`), which may well be a todo line or
           a status update with no envelope on it.
        3. They replied to the message that started it all -- their own
           upload -- from the main timeline. That event carries no
           envelope of ours, but it roots the thread our filing answered
           in, so the thread hanging off it identifies the item.

        Returns None when nothing in reach is a filing of ours, which is
        the common case: most messages are searches.
        """
        parent_id = self._in_reply_to_id(event)
        envelope = await self._reply_parent_envelope(room_id, event)
        if envelope is not None and self._is_filing_envelope(envelope):
            return (parent_id, envelope)

        roots = [r for r in (self.get_thread_root(event), parent_id) if r]
        for root in dict.fromkeys(roots):
            for event_id, candidate in await self._thread_envelopes(room_id, root):
                if self._is_filing_envelope(candidate):
                    return (event_id, candidate)
        return None

    @staticmethod
    def _correction_target_doc_id(envelope: dict) -> int | None:
        """The paperless_id a document filing envelope points at."""
        if envelope.get("type") not in ("document.filed", "document.reclassified"):
            return None
        paperless_id = envelope.get("data", {}).get("paperless_id")
        return paperless_id if isinstance(paperless_id, int) else None

    @staticmethod
    def _correction_target_capture_path(envelope: dict) -> str | None:
        """The vault path a capture filing envelope points at."""
        if envelope.get("type") not in ("capture.filed", "capture.reclassified"):
            return None
        vault_path = envelope.get("data", {}).get("vault_path")
        return vault_path if isinstance(vault_path, str) and vault_path else None

    async def _handle_correction(
        self, room_id: str, event, anchor: tuple[str, dict], reply_to: str,
        *, ctx: RoomContext,
    ) -> bool:
        """Re-run classification for the item `anchor` names, using the
        user's message as an authoritative hint. Returns whether the
        message was consumed as a correction.

        One entry point for both filing kinds: the envelope says whether
        the item is a Paperless document or a vault capture, and the
        correction chain is collected the same way for either. A message
        that turns out to carry no correction (a bare quote with no words
        of its own) is handed back to normal routing.
        """
        anchor_id, envelope = anchor
        hint, initial = await self._collect_correction_chain(
            room_id, event, anchor_id, envelope,
        )
        if not hint:
            return False

        doc_id = self._correction_target_doc_id(envelope)
        capture_path = self._correction_target_capture_path(envelope)
        if doc_id is None and capture_path is None:
            return False

        await self._acknowledge_correction(room_id, reply_to)
        if doc_id is not None:
            await self._handle_reply_reprocess(
                room_id, doc_id, hint, reply_to, ctx=ctx,
                date_filed=event_date(event),
                initial_classification=initial,
            )
            return True

        await self._handle_reply_capture_reprocess(
            room_id, capture_path, hint, event.sender, reply_to, ctx=ctx,
            initial_classification=initial,
            capture_id=envelope.get("data", {}).get("capture_id"),
            reply_text=_strip_reply_fallback(event.body or ""),
            reply_id=getattr(event, "event_id", "") or "",
            reply_at=time_of_ms(getattr(event, "server_timestamp", 0)),
        )
        return True

    async def _acknowledge_correction(self, room_id: str, reply_to: str) -> None:
        """Show that the correction was taken: 👀 on it and typing.

        Called only once the message is known to be a correction (it has
        words and points at a filing), so a question or a thank-you in
        the same thread gets no reaction. A filing shows the same pair
        when it starts. The typing indicator ends with the answer's send.
        """
        await self._react(room_id, reply_to, EYES)
        await self._set_typing(room_id, on=True)

    async def _collect_correction_chain(
        self, room_id: str, event, anchor_id: str, anchor_envelope: dict,
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

        The anchor envelope (`_correction_anchor`'s answer: the latest
        classification of this item the user could see) carries the
        post-correction-N state under ``data``. That's the picture the
        human had on screen when they typed their note, so it's the
        right anchor for "apply this correction as a delta": the LLM
        works against what the user saw, not against the LLM's
        untouched first pass. Each step (state_N + correction_(N+1) →
        state_(N+1)) is deterministic; chaining them gives a
        deterministic transform from the initial filing to the current
        correction.

        Returned hint: numbered list when more than one correction is
        present, plain string for a single correction.
        """
        bodies: list[str] = []
        current_body = _strip_reply_fallback(event.body)
        if current_body:
            bodies.append(current_body)

        data = anchor_envelope.get("data")
        latest_state: dict | None = data if isinstance(data, dict) else None
        parent_id = anchor_id

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
            resp = await self.client.room_get_event(room_id, event_id)
        except Exception as e:
            logger.debug("[archivist] event fetch failed for {}: {}", event_id, e)
            return None
        return getattr(resp, "event", None)

    async def _handle_reply_reprocess(
        self, room_id: str, doc_id: int, user_hint: str, reply_to: str,
        *, ctx: RoomContext, date_filed: str | None = None,
        initial_classification: dict | None = None,
    ) -> None:
        """Re-enrich `doc_id` with the user's reply as a correction hint.

        The pipeline does the work and returns a ReprocessOutcome; this
        maps it to a chat reply. The reclassified confirmation carries a
        fresh `document.reclassified` envelope so the user can chain
        another correction by replying to it.

        Answers go back through `_answer`, so they land in the same
        thread the correction came from. That placement is load-bearing
        rather than tidiness: the next correction resolves its anchor by
        asking the thread for the newest classification, so a
        confirmation posted outside the thread would leave every later
        correction anchored to the original filing.
        """
        o = await self._services.pipeline.reprocess(
            doc_id=doc_id, user_hint=user_hint, date_filed=date_filed,
            initial_classification=initial_classification,
            write_in=self._rooms.write_in(ctx),
        )
        # The terminal glyph beside the 👀 `_handle_correction` put there.
        await self._react(
            room_id, reply_to,
            CHECK if o.status == "reclassified" else CROSS,
        )
        if o.status == "doc_missing":
            await self._answer(
                room_id, self.t("reprocess_doc_missing", doc_id=doc_id), reply_to,
            )
        elif o.status == "llm_error":
            kind, detail = o.llm_error
            await self._answer(
                room_id,
                self.t("reprocess_llm_error", doc_id=doc_id, kind=kind, detail=detail),
                reply_to,
            )
        else:  # reclassified
            reply = render_reprocessed_reply(
                self.t,
                title=o.title,
                doc_id=doc_id,
                resolved_persons=o.resolved_persons,
                resolved_type=o.resolved_type,
                resolved_correspondent=o.resolved_correspondent,
            )
            await self._answer(
                room_id, reply, reply_to,
                metadata={"dev.famstack.event": o.envelope},
            )

    async def _handle_reply_capture_reprocess(
        self, room_id: str, vault_path: str, user_hint: str,
        sender_mxid: str, reply_to: str,
        *, ctx: RoomContext, initial_classification: dict | None = None,
        capture_id: str | None = None,
        reply_text: str = "",
        reply_id: str = "",
        reply_at: str = "",
    ) -> None:
        """Re-classify a capture with the user's reply as a hint.

        Parallels `_handle_reply_reprocess` for documents: dispatch to
        the pipeline, render via the shared reprocessed presenter,
        attach the fresh `capture.reclassified` envelope so the user
        can chain another correction by replying to THIS message.

        ``capture_id`` is the event the capture was filed from. Its
        caption is read off that event again, so a correction restores
        a caption an earlier filing dropped. ``reply_text`` is the words
        of this reply, which the entry keeps as written; a note keeps it as
        a source sent as ``reply_id`` at ``reply_at``.
        """
        upload = await self._fetch_event(room_id, capture_id)
        caption = (
            _attachment_caption(upload.source.get("content", {}))
            if upload is not None else ""
        )
        outcome = await self._services.capture.reprocess(
            vault_path=vault_path, user_hint=user_hint,
            sender_mxid=sender_mxid,
            initial_classification=initial_classification,
            write_in=self._rooms.write_in(ctx),
            caption=caption,
            reply_text=reply_text,
            capture_id=capture_id,
            reply_id=reply_id, reply_at=reply_at,
        )
        # `_reply_for_capture` already knows how to render the
        # reclassified branch + attach the envelope, so we just defer.
        await self._captures.reply(
            room_id, outcome, reply_to, corrected_from=initial_classification,
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

        # A photo or a recording in the memories room is a memory, not a
        # document: the diary files it, and the archivist leaves it be.
        # Asked of the room's identity, before the config read, so the
        # family's own room costs no account-data read.
        if self._diary.is_room(self._room_context(room)):
            return
        ctx = await self._message_context(room)
        # First-encounter welcome -- idempotent, gated by a per-room
        # state event. Runs ahead of the routing decision so the user
        # always sees the intro before any other reply from the bot.
        await self._rooms.welcome_if_needed(room, ctx)
        mentioned = self._is_bot_mentioned(event)
        if not self._should_react(ctx, mentioned=mentioned):
            logger.debug(
                "[archivist] skipping file from {} in {} per room mode",
                event.sender, ctx.room_id,
            )
            return

        # An upload dropped into someone else's thread is material for
        # that conversation, not for us. Same gate as `_on_text`.
        if not mentioned and not await self._thread_is_ours(room.room_id, event):
            return

        # On modern Matrix clients an upload can carry a human caption
        # alongside the file (Element X, FluffyChat, anything honoring
        # MSC4274). When present, the caption rides into the classify
        # prompt as user_hint -- "neue Personalausweise für Marge und
        # Bart" steers the LLM the same way a reply-to-correct does,
        # without the user having to wait for a bad classification first.
        # A capture also keeps the caption on its entry, as written.
        raw_filename = content.get("filename") or content.get("body") or "document"
        caption = _attachment_caption(content)
        display_name = _clean_filename(raw_filename, msgtype)
        reply_to = event.event_id

        # Multi-page scan mode: accumulate pages until `)` closes the
        # session and the batch is filed as one document.
        if event.sender in self._scan_sessions:
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
        # capture room: a PDF becomes a bookmark and a photo a note, in
        # the room's topic or the sender's bucket, linked to the mxc URL
        # and with a copy of the bytes in the media archive.
        if self._rooms.is_documents_room(ctx):
            await self._documents.file(
                room.room_id, raw_filename, display_name, file_data, reply_to,
                ctx=ctx, date_filed=event_date(event),
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
            await self._captures.capture_binary(
                ctx=ctx,
                room_id=room.room_id,
                file_data=file_data,
                mime=content.get("info", {}).get("mimetype")
                     or guess_mime(raw_filename, msgtype),
                filename=raw_filename,
                source_uri=url,
                sender_mxid=event.sender,
                capture_id=event.event_id,
                reply_to=reply_to,
                default_person=not bot_attachment,
                extra_seed_topics=extra_seed_topics,
                captured_ts=getattr(event, "server_timestamp", 0) or 0,
                caption=caption,
            )

    async def _on_text(self, room, event: RoomMessageText) -> None:
        if event.sender == self.user_id:
            return

        # The memories room is the family's; only replies in our card
        # threads are ours, and they are corrections (diary_room.py).
        if self._diary.is_room(self._room_context(room)):
            await self._diary.on_text(room, event)
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
        # Whether these words were spoken rather than typed. Used twice
        # below, and for nothing else: never to route, only to decide
        # whether the message was handed to us deliberately and whether
        # to quote it back for checking.
        transcribed = voice.was_transcribed(event.source.get("content", {}))

        ctx = await self._message_context(room)
        # First-encounter welcome -- idempotent, gated by a per-room
        # state event. Runs ahead of the routing decision so the user
        # always sees the intro before any other reply from the bot.
        await self._rooms.welcome_if_needed(room, ctx)
        mentioned = self._is_bot_mentioned(event)
        is_documents = self._rooms.is_documents_room(ctx)
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

        # Inside a thread, only our own -- see `_thread_is_ours`. Checked
        # before every routing branch so a message in someone else's
        # conversation is neither filed nor read as a correction.
        if not mentioned and not await self._thread_is_ours(room.room_id, event):
            return

        # When the bot is mentioned, the mxid is conversational noise —
        # strip it so the remaining body drives command matching and
        # the search query. A bare ping with no content becomes "help"
        # so the user gets a useful response instead of an empty search.
        if mentioned:
            formatted_body = event.source.get("content", {}).get("formatted_body")
            query = self.strip_mention(
                query, self.user_id, formatted_body=formatted_body,
                display_name=self.display_name,
            )
            if not query:
                query = "help"
            query_lower = query.lower()

        # ── Correcting a prior filing ────────────────────────────────
        # A message that lands on one of our filings -- quoting it, or
        # written in the thread it lives in -- WITHOUT @-mentioning the
        # bot is a correction: `_correction_anchor` finds which item,
        # and the classifier re-runs with the user's words as an
        # authoritative hint.
        #
        # An @-mention is a different intent: the user is addressing
        # the bot conversationally (search, help), and that stays true
        # inside a filing thread, where "@archivist what else is from
        # Duff?" is a question about the archive rather than a note on
        # the document. Deliberate address beats ambient context, and
        # it keeps the guard some clients need -- Element X attaches an
        # `m.in_reply_to` to mentioned messages the user never aimed,
        # and without this those searches would be eaten by reprocess.
        if not mentioned:
            anchor = await self._correction_anchor(room.room_id, event)
            if anchor is not None and await self._handle_correction(
                room.room_id, event, anchor, reply_to, ctx=ctx,
            ):
                return

        if query_lower in HELP_COMMANDS:
            # Same per-room welcome the bot posted on first encounter
            # -- the user re-reads exactly what fits where they asked.
            await self._send(
                room.room_id,
                self._rooms.welcome_text_for(room, ctx),
                reply_to,
            )

        # `(` and `scan` open a multi-page session and accept an
        # optional caption inline: `( neue Personalausweise`. The
        # caption rides through to classify alongside any per-page
        # captions and the closer's trailing text.
        elif (begin := _split_scan_command(query, SCAN_BEGIN))[0]:
            sender_name = event.sender.split(":")[0].replace("@", "").capitalize()
            self._scan_sessions[event.sender] = {
                "files": [], "room_id": room.room_id, "caption": begin[1],
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
                    room.room_id, event.sender, reply_to, ctx=ctx,
                    date_filed=event_date(event),
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
                await self._documents.file_url(
                    room.room_id, query, reply_to, ctx=ctx,
                    date_filed=event_date(event),
                    submitter_mxid=event.sender,
                )
            else:
                await self._captures.capture_url(
                    room.room_id, query, event.sender, reply_to, ctx=ctx,
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
                await self._documents.file_url(
                    room.room_id, embedded_url, reply_to, ctx=ctx,
                    date_filed=event_date(event),
                    submitter_mxid=event.sender,
                )
            else:
                await self._captures.capture_url(
                    room.room_id, embedded_url, event.sender, reply_to, ctx=ctx,
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

        elif transcribed or (
            self._looks_like_paste(query) and self._rooms.count_humans(room) < 2
        ):
            # One person in the room, paste-shaped message, no mention →
            # file as text capture. Nobody is being talked *to* here, so
            # a long message is material dropped for us to keep.
            #
            # Above one human the room is a conversation, and length
            # stops meaning anything: people write long messages to each
            # other all day and almost none of it is meant to be kept.
            # Guessing there produced notes titled after somebody's error
            # message. So the ambient path is off and 📌 is how you keep
            # something -- explicit, visible in the timeline, and already
            # the one trigger that ignores every room mode.
            #
            # Links and files are unaffected in either case: pasting a URL
            # or dropping a PDF is a deliberate act, not a turn in a
            # conversation. A voice memo is the same kind of act: nobody
            # holds the mic button for thirty seconds as a turn in a chat,
            # and the length heuristic that stands in for deliberateness
            # elsewhere cannot read it. So speech carries itself past this
            # gate, in any room, exactly as an upload does.
            #
            # This is not speech being routed differently. Everything
            # above still applies to it unchanged -- a memo addressed to
            # the bot is a question, in the documents room it is a search,
            # in a thread it obeys ownership. Only "was this handed to us
            # on purpose?" is answered another way, because for speech
            # there is another way to answer it.
            await self._captures.capture_text(
                room.room_id, query, event.sender, reply_to, ctx=ctx,
                capture_id=event.event_id, transcribed=transcribed,
                sent_at=time_of_ms(getattr(event, "server_timestamp", 0)),
                caption=_attachment_caption(
                    event.source.get("content", {}).get(voice.TRANSCRIPT_KEY) or {}
                ),
            )

        elif self.get_thread_root(event) is not None:
            # A message in one of our threads (`_thread_is_ours` let it
            # through) is addressed to us, and nothing above could place
            # it: no filing of ours is readable there. Say so, because
            # silence reads as a correction that was taken.
            await self._answer(room.room_id, self.t("correction_no_filing"), reply_to)

        else:
            # Chat, and nothing else matched. Either short (a capture
            # room's own threshold) or long in a room with other people
            # in it, where keeping something is 📌 rather than a guess.
            logger.debug(
                "[archivist] {} ignored text from {}: {!r}",
                room.room_id, event.sender, query[:60],
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
            "📎": self._react_archive_source,
            "📄": self._react_archive_source,
            "🔁": self._react_retry,
            "🔄": self._react_retry,
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
        if self._diary.is_room(self._room_context(room)):
            return
        emoji = self.normalize_emoji(getattr(event, "key", ""))
        handler = self._reaction_handlers().get(emoji)
        if handler is None:
            return
        target_id = getattr(event, "reacts_to", None)
        if not target_id:
            return
        try:
            resp = await self.client.room_get_event(room.room_id, target_id)
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
        ctx = await self._message_context(room)
        if _is_just_url(body):
            await self._captures.capture_url(
                room.room_id, body, author, target_id, ctx=ctx,
                capture_id=target_id,
            )
        elif (embedded_url := _first_url(body)) is not None:
            hint = body.replace(embedded_url, "", 1).strip(" \t\n\r:.,;!?—-")
            await self._captures.capture_url(
                room.room_id, embedded_url, author, target_id, ctx=ctx,
                capture_id=target_id, user_hint=hint or None,
            )
        else:
            await self._captures.capture_text(
                room.room_id, body, author, target_id, ctx=ctx,
                capture_id=target_id,
                sent_at=time_of_ms(getattr(target, "server_timestamp", 0)),
            )

    async def _react_archive_source(self, room, event, target, target_id) -> None:
        """📎 / 📄 — archive a whole ingest source card as one Paperless doc.

        Generic over `dev.famstack.source` cards (email today, another ingest
        channel tomorrow): page one is the header the producing bot rendered
        (the card body) plus the card's verbatim content, then every
        attachment the producer threaded under this card follows as pages.
        The card's `source` value becomes a flat provenance tag and the header
        rides along as a Paperless note, so the sender/subject are found by
        full-text search. This reads only the generic source contract — it
        does not parse the producer's domain fields (email's From/Subject
        live in the header the mail bot already rendered). 📎 on a non-source
        message is a no-op. Reactions replay at-least-once, so Paperless's own
        duplicate detection makes a repeat 📎 a no-op rather than a copy.
        """
        content = (getattr(target, "source", {}) or {}).get("content", {}) or {}
        source = content.get(self.SOURCE_KEY)
        if not isinstance(source, dict):
            return
        header = (content.get("body") or "").strip()
        body = (source.get("raw_content") or "").strip()
        source_type = (source.get("source") or "").strip() or "source"
        if not header and not body:
            return

        await self._react(room.room_id, target_id, EYES)
        await self._set_typing(room.room_id, on=True)
        attachments = await self._collect_source_attachments(room.room_id, target_id)
        pdf = build_source_pdf(header, body, attachments)

        title_hint = next(
            (ln.strip("# *_>-\t ") for ln in header.splitlines()
             if ln.strip("# *_>-\t ")),
            source_type,
        )
        safe = "".join(
            ch if ch.isalnum() or ch in " .-_" else "_" for ch in title_hint
        ).strip()
        filename = f"{(safe[:80] or source_type)}.pdf"
        await self._documents.file(
            room.room_id, filename, title_hint[:120] or source_type, pdf,
            ctx=await self._message_context(room),
            reply_to=target_id, date_filed=source.get("captured_at") or None,
            submitter_mxid=event.sender, user_hint=title_hint or None,
            extra_tags=[source_type], note_prefix=header or None,
        )

    async def _react_retry(self, room, event, target, target_id) -> None:
        """🔁 / 🔄 — do the work on this message again.

        Filing fails for reasons that have nothing to do with the message:
        the model was still loading, Paperless was restarting, the handler
        ran past its timeout. Rather than a retry queue somebody has to
        drain, the recovery gesture is the same shape as every other one
        here: react on the message that failed. The ❌ the framework left
        tells you which message; this tells you what to do about it.

        Retry re-dispatches the handler that owns that kind of message, so
        there is nothing to keep in sync as bindings are added. Both paths
        are idempotent (Paperless dedups on content, captures key on the
        target event id), so retrying something that actually succeeded is
        a no-op rather than a second copy.
        """
        content = (getattr(target, "source", {}) or {}).get("content", {}) or {}
        if isinstance(content.get(self.SOURCE_KEY), dict):
            await self._react_archive_source(room, event, target, target_id)
        elif content.get("msgtype") in SUPPORTED_MSGTYPES:
            await self._on_file(room, target)

    async def _collect_source_attachments(
        self, room_id: str, card_id: str, max_pages: int = 5,
    ) -> list[SourceAttachment]:
        """Gather the attachments a producer threaded under a source card.

        Attachments are ordinary `m.file`/`m.image` events whose
        `dev.famstack.attachment` marker back-references this card via
        `source_event`. We page the timeline backwards (attachments are posted
        just after the card, so they are newer) collecting matches until we
        reach the card itself. Bounded by `max_pages` so a busy room can't
        make the walk run forever; if the card scrolled out of reach we file
        whatever we found, since the body page always survives.
        """
        if self._client is None:
            return []
        out: list[SourceAttachment] = []
        start = ""
        for _ in range(max_pages):
            try:
                resp = await self._client.room_messages(
                    room_id, start=start, limit=50,
                    message_filter={"types": ["m.room.message"]},
                )
            except Exception as e:
                logger.debug("[archivist] source attachment scan failed: {}", e)
                break
            chunk = getattr(resp, "chunk", None) or []
            if not chunk:
                break
            for ev in chunk:
                c = (getattr(ev, "source", {}) or {}).get("content", {}) or {}
                marker = c.get(self.ATTACHMENT_KEY)
                if isinstance(marker, dict) and marker.get("source_event") == card_id:
                    url = c.get("url", "")
                    if isinstance(url, str) and url.startswith("mxc://"):
                        data = await self._download_media(url)
                        if data:
                            out.append(SourceAttachment(
                                filename=c.get("filename") or c.get("body") or "attachment",
                                mimetype=(c.get("info") or {}).get("mimetype") or "",
                                data=data,
                            ))
                if getattr(ev, "event_id", None) == card_id:
                    return out
            start = getattr(resp, "end", None) or ""
            if not start:
                break
        return out

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
            seed_topics.append(f"{EMAIL_SENDER_TAG}{source['from']}")
        if source.get("account"):
            seed_topics.append(f"{EMAIL_MAILBOX_TAG}{source['account']}")
        if source.get("folder"):
            seed_topics.append(f"{EMAIL_FOLDER_TAG}{source['folder']}")

        # Scope-aware placement: email inherits the bucket of the room it
        # lands in, then nests under emails/ (kind="email"). The family room
        # has no binding -> shared_bucket -> family/emails/; a topic room
        # ("Family E-Mails", "Hobby") routes to its bucket so a mailbox's mail
        # is filed by the scope of its room, co-located with that room's
        # attachments. The topic tag rides along as a seed.
        binding = await self._rooms.topic_binding(room, event.sender)
        if binding:
            bucket = binding.bucket
            if binding.seed_topics:
                seed_topics.extend(binding.seed_topics)
        else:
            # No topic: scope by room membership (a DM/private room → its
            # human; the shared family room → the shared bucket).
            bucket = self._rooms.scope_bucket(room)
        outcome = await self._services.capture.capture_email(
            subject=source.get("subject"),
            body=body,
            message_id=source.get("message_id"),
            thread_root=source.get("thread_root"),
            sender_mxid=event.sender,
            from_addr=source.get("from"),
            from_name=source.get("from_name"),
            captured_at=source.get("captured_at"),
            bucket=bucket,
            seed_topics=seed_topics or None,
        )
        logger.info(
            "[archivist] email folded ({}) -> {}",
            outcome.status, outcome.vault_path,
        )

        await self._confirm_email_filing(room.room_id, event, outcome)

    # Capture statuses that mean the email is in the memory.
    EMAIL_FILED_STATUSES = ("captured", "reclassified")

    async def _confirm_email_filing(self, room_id: str, event, outcome) -> None:
        """Show the outcome of an email's filing on the email itself.

        The email gets ✅ when it is in the memory and ❌ when it is not;
        an `empty` email did nothing and gets neither. The words go into
        the email's thread, where the mail bot already put its full text:
        the filed card (`render_capture_reply`) carrying the filing's
        envelope, or the reason it was not filed. The room's timeline
        gets no message of ours, because one confirmation per email would
        fill the mail room.

        The envelope must stay on that message: the correction path finds
        the filing through the thread hanging off the email
        (`_correction_anchor`), and reprocess reads it from there.

        A later email of the same conversation is itself posted inside the
        first email's thread, and Matrix has no nested threads, so the
        confirmation joins that thread and quotes the email it is about.
        """
        if outcome.status == "empty":
            return
        thread = self.get_thread_root(event) or event.event_id
        if outcome.status not in self.EMAIL_FILED_STATUSES:
            await self._react(room_id, event.event_id, CROSS)
            if outcome.status == "not_filed":
                words = self.t("capture_not_filed", reason=why(self.language, outcome.failure_reason))
            elif outcome.status == "no_mirror":
                words = self.t("capture_no_mirror")
            else:
                words = self.t("email_not_filed")
            await self._send(
                room_id, words,
                reply_to=event.event_id, thread_root_event_id=thread,
            )
            return
        await self._react(room_id, event.event_id, CHECK)
        if outcome.envelope:
            reply = render_capture_reply(
                self.t, source_title_hint=None,
                classification=shown_in_chat(outcome.classification),
                link=self._captures.capture_link(outcome),
                card_type=card_type(outcome.envelope),
                captured_at=outcome.captured_at, language=self.language,
            )
            await self._send(
                room_id, reply,
                reply_to=event.event_id,
                thread_root_event_id=thread,
                metadata={self.FAMSTACK_EVENT_KEY: outcome.envelope, **NO_MENTIONS},
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

    async def _handle_scan_complete(
        self, room_id: str, sender: str, reply_to: str | None = None,
        *, ctx: RoomContext, date_filed: str | None = None,
    ):
        session = self._scan_sessions.pop(sender)
        files = session["files"]
        caption = session.get("caption", "").strip()
        sender_name = sender.split(":")[0].replace("@", "").capitalize()

        # Nothing accumulated -- the user opened a session and closed it
        # without sending anything. Treat as cancelled, same as today.
        if not files:
            await self._send(room_id, self.t("scan_cancelled"), reply_to)
            return

        if files:
            if len(files) == 1:
                filename, file_data = files[0]
                display_name = _clean_filename(filename)
                await self._send(
                    room_id, self.t("scan_complete_single"), reply_to,
                )
                await self._documents.file(
                    room_id, filename, display_name, file_data, reply_to,
                    ctx=ctx, date_filed=date_filed,
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
                await self._documents.file(
                    room_id, filename, display_name, pdf_data, reply_to,
                    ctx=ctx, date_filed=date_filed,
                    user_hint=caption or None,
                    submitter_mxid=sender,
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
            binding = await self._rooms.topic_binding(
                self._rooms.room_by_id(room_id), sender,
            )
            if binding is not None:
                topic_bucket = binding.bucket

        reply = await self._services.search.run(
            query=query, sender=sender,
            notifier=self._captures.notifier(room_id, reply_to),
            topic_bucket=topic_bucket,
        )
        await self._send(room_id, reply, reply_to)

    # ── Show document content ─────────────────────────────────────────

    async def _handle_show(self, room_id: str, doc_id: int, reply_to: str | None = None):
        """Fetch a document's content from Paperless and return it as Markdown."""
        doc = await self._services.paperless.get_doc(doc_id)
        if not doc:
            await self._send(room_id, self.t("show_not_found", doc_id=doc_id), reply_to)
            return

        title = doc.get("title", self.t("show_untitled"))
        content = doc.get("content", "").strip()
        link = await self._documents.document_link(doc_id)

        if not content:
            await self._send(room_id, self.t("show_no_text", title=title, link=link), reply_to)
            return

        # Matrix has message size limits — truncate long documents
        if len(content) > 4000:
            content = content[:4000] + "\n\n" + self.t("show_truncated")

        lines = [f"**{title}**", "", content]
        if link:
            lines.extend(["", link])

        await self._send(room_id, "\n".join(lines), reply_to)
