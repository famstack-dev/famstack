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
import os
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
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
from stack.bot import EYES, MicroBot, normalize_alias, voice
from stack.ai.client import (
    ModelCapabilities,
)
from stack.cards.sources import time_of_ms

from .archivist_captures import ArchivistCaptures, guess_mime
from .archivist_corrections import ArchivistCorrections
from .archivist_documents import SUPPORTED_MSGTYPES, ArchivistDocuments, event_date
from .archivist_reactions import ArchivistReactions
from .archivist_rooms import ArchivistRooms
from .archivist_scan import ArchivistScan
from .archivist_search import ArchivistSearch
from .archivist_sources import ArchivistSources
from .archivist_texts import HELP_COMMANDS, SCAN_BEGIN, SCAN_END, language_option, t
from .text_utils import (
    attachment_caption as _attachment_caption,
    clean_filename as _clean_filename,
    first_url as _first_url,
    is_just_url as _is_just_url,
    looks_like_paste,
    split_scan_command as _split_scan_command,
)
from .diary_room import DEFAULT_JOB_AT, DiaryRoom
from .document_pipeline import (
    DocumentPipeline,
)
from .search_service import SearchService
from .capture_pipeline import CapturePipeline
from .vault_context import VaultContext


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
        self._sources = ArchivistSources(
            self, rooms=self._rooms, captures=self._captures,
            services=lambda: self._services,
        )
        self._scan = ArchivistScan(self, documents=self._documents)
        self._reactions = ArchivistReactions(
            self, captures=self._captures, documents=self._documents,
            sources=self._sources,
        )
        self._search = ArchivistSearch(
            self, rooms=self._rooms, documents=self._documents,
            captures=self._captures, services=lambda: self._services,
        )
        self._corrections = ArchivistCorrections(
            self, rooms=self._rooms, captures=self._captures,
            services=lambda: self._services,
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
        if self._scan.is_open(event.sender):
            await self._scan.add_page(
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
            await self._sources.file_source(room, event, source)
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
            anchor = await self._corrections.anchor(room.room_id, event)
            if anchor is not None and await self._corrections.handle(
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
            await self._scan.open(room.room_id, event.sender, begin[1], reply_to)

        elif (end := _split_scan_command(query, SCAN_END))[0]:
            await self._scan.close(
                room.room_id, event.sender, end[1], reply_to, ctx=ctx,
                date_filed=event_date(event),
            )

        elif query_lower.startswith("show ") and query[5:].strip().isdigit():
            await self._search.show(room.room_id, int(query[5:].strip()), reply_to)

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
            await self._search.search(
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
        handler = self._reactions.handlers().get(emoji)
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





















