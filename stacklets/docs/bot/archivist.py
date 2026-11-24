"""archivist: the document filing bot, a router over its collaborators.

    # loaded by the bot runner from bot/bot.toml
    bot = ArchivistBot(homeserver, user_id, password, session_dir, **kw)
    await bot.start()

A person sends a photo of a letter, a PDF, a link or a pasted text to a
chat room. In the documents room it goes to Paperless: OCR, classification
by the AI, tags, a card in the family memory, and a reply with a link. In
every other room it becomes a capture: a bookmark or a note, written as a
card into the room's topic or the sender's bucket. A reply on a filing
corrects it; `(` and `)` combine a multi-page scan; any other text in the
documents room, or to the bot by name, searches.

This module holds what a MicroBot must: the settings, the callbacks,
`start`, and the routing of `_on_file`, `_on_text` and `_on_reaction`.
Each topic is a collaborator built in `__init__`, like `DiaryRoom`:

    _on_file ──────┬──> ArchivistScan         a page of an open scan
                   ├──> ArchivistDocuments    Paperless, the filing reply
                   └──> ArchivistCaptures     a capture, its reply
    _on_text ──────┬──> ArchivistCorrections  a reply on one of our filings
                   ├──> ArchivistSources      an email the mail bot posted
                   ├──> ArchivistScan         `(` and `)`
                   ├──> ArchivistSearch       search, `show <n>`
                   └──> ArchivistDocuments, ArchivistCaptures  a link, a paste
    _on_reaction ──────> ArchivistReactions   🔖 keep, 📎 to Paperless, 🔁 retry

    ArchivistRooms     which room is which: documents, topic, scope, welcome
    archivist_texts    the chat's words, in the household language
    archivist_services the services the work is handed to (Paperless, ...)

A collaborator gets the services it uses and its siblings explicitly. It
gets this bot as its chat port (send, react, answer, the client, `t`),
never this module, because the bot runner and the tests replace those
methods on the bot after it is built. The MicroBot base class does the
Matrix login, the encryption and the sync loop.
"""

import asyncio
import os
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

from .pdf_analysis import (
    DEFAULT_REFORMAT_MAX_PDF_PAGES,
    DEFAULT_VISION_MAX_PDF_PAGES,
)
from .pipeline import (
    DEFAULT_CLASSIFY_MAX_CHARS,
)
from stack import resolve_model
from stack.bot import EYES, MicroBot, normalize_alias, voice
from stack.cards.sources import time_of_ms

from .archivist_captures import ArchivistCaptures
from .archivist_corrections import ArchivistCorrections
from .archivist_documents import SUPPORTED_MSGTYPES, ArchivistDocuments, event_date
from .archivist_reactions import ArchivistReactions
from .archivist_rooms import ArchivistRooms
from .archivist_services import ArchivistServices, build_services
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


# ── ArchivistBot ─────────────────────────────────────────────────────────────

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
        self._read_environment()
        # `!config language`: the language a room's filings are written in.
        # Built here because its description names the household language.
        self.CONFIG_OPTIONS = {
            **MicroBot.CONFIG_OPTIONS,
            "language": language_option(self.language),
        }
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
        self._read_settings(settings)
        self._build_collaborators()
        self._paperless_version: str = ""

    def _read_environment(self) -> None:
        """Read the stack's config the bot runner rendered into the env."""
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

    def _read_settings(self, settings: dict) -> None:
        """Read the bot's own settings (`bot.toml`, `[settings]`)."""
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

    def _build_collaborators(self) -> None:
        """Build one collaborator per topic, siblings handed in explicitly."""
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
            self._services = build_services(self)
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
    #
    # The three callbacks route; the collaborators do the work. A text
    # passes its gates (our own, the memories room, a bot's source, the
    # room mode, someone else's thread) in `_on_text`, then
    # `_route_text` reads it as a correction, a command or content.

    async def _on_file(self, room, event) -> None:
        """An upload: a page of an open scan, a document in the documents
        room, or a capture in any other room."""
        content = self._upload_content(event)
        if content is None:
            return
        # A photo or a recording in the memories room is a memory, not a
        # document: the diary files it, and the archivist leaves it be.
        # Asked of the room's identity, before the config read, so the
        # family's own room costs no account-data read.
        if self._diary.is_room(self._room_context(room)):
            return
        ctx = await self._message_context(room)
        # First-encounter welcome -- idempotent, gated by the room's
        # history. Runs ahead of the routing decision so the user
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
        await self._file_upload(room, event, content, ctx)

    def _upload_content(self, event) -> dict | None:
        """The upload's content when it is a file or an image someone else
        sent with an `mxc://` address; None for anything else."""
        if event.sender == self.user_id:
            return None
        content = event.source.get("content", {})
        if content.get("msgtype", "") not in SUPPORTED_MSGTYPES:
            return None
        url = content.get("url", "")
        if not url or not url.startswith("mxc://"):
            return None
        return content

    async def _file_upload(self, room, event, content: dict, ctx) -> None:
        """Download an upload and hand it on: to the open scan, to Paperless
        in the documents room, or to the capture pipeline elsewhere."""
        msgtype = content.get("msgtype", "")
        url = content["url"]
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
            await self._scan.add_page(room.room_id, event, url, raw_filename, caption)
            return

        file_data = await self._download_media(url)
        if not file_data:
            await self._send(room.room_id, self.t("download_failed_matrix", name=display_name), reply_to)
            return

        # Acknowledge the upload with a 👀 reaction on the source message
        # the moment work starts, then set typing: a reaction is the last
        # send before the work, so the indicator stays alive through the
        # OCR + classify + mirror work that follows. The filing reply (or
        # an error reply) is the real closure signal.
        await self._react(room.room_id, reply_to, EYES)
        await self._set_typing(room.room_id, on=True)

        # Documents room → full archivist pipeline (Paperless + classify
        # + entity reconciliation). Every other reacting room is a
        # capture room (`ArchivistCaptures.capture_upload`).
        if self._rooms.is_documents_room(ctx):
            await self._documents.file(
                room.room_id, raw_filename, display_name, file_data, reply_to,
                ctx=ctx, date_filed=event_date(event),
                submitter_mxid=event.sender,
                user_hint=caption or None,
            )
        else:
            await self._captures.capture_upload(
                ctx=ctx, room_id=room.room_id, event=event, content=content,
                file_data=file_data, filename=raw_filename, caption=caption,
            )

    async def _on_text(self, room, event: RoomMessageText) -> None:
        """A text: a correction to the diary, a bot's source, or a message
        for the archivist, routed by `_route_text` once its gates pass."""
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

        ctx = await self._message_context(room)
        # First-encounter welcome, ahead of the routing decision, as for files.
        await self._rooms.welcome_if_needed(room, ctx)
        mentioned = self._is_bot_mentioned(event)
        if not await self._text_is_ours(room, event, ctx, mentioned=mentioned):
            return
        if mentioned:
            query = self._without_mention(query, event)
        await self._route_text(room, event, query, ctx, mentioned=mentioned)

    async def _text_is_ours(self, room, event, ctx, *, mentioned: bool) -> bool:
        """Whether the room mode and the thread let this text through."""
        logger.info(
            "[archivist] text from {} in {} (alias={!r}, dm={}, docs={}, "
            "members={}, mention={})",
            event.sender, ctx.room_id, ctx.alias, ctx.is_dm,
            self._rooms.is_documents_room(ctx), len(ctx.members), mentioned,
        )
        if not self._should_react(ctx, mentioned=mentioned):
            logger.debug(
                "[archivist] skipping {} in {} per room mode",
                event.sender, ctx.room_id,
            )
            return False

        # Inside a thread, only our own -- see `_thread_is_ours`. Checked
        # before every routing branch so a message in someone else's
        # conversation is neither filed nor read as a correction.
        return mentioned or await self._thread_is_ours(room.room_id, event)

    def _without_mention(self, query: str, event) -> str:
        """The message without the bot's mention; "help" for a bare ping.

        When the bot is mentioned, the mxid is conversational noise, so
        the rest drives command matching and the search query. A bare
        ping becomes "help", so the user gets a useful response instead
        of an empty search.
        """
        formatted_body = event.source.get("content", {}).get("formatted_body")
        query = self.strip_mention(
            query, self.user_id, formatted_body=formatted_body,
            display_name=self.display_name,
        )
        return query or "help"

    async def _route_text(self, room, event, query: str, ctx, *, mentioned: bool) -> None:
        """Read a text that passed its gates: a correction, a command, or content."""
        # ── Correcting a prior filing ────────────────────────────────
        # A message that lands on one of our filings -- quoting it, or
        # written in the thread it lives in -- WITHOUT @-mentioning the
        # bot is a correction: `ArchivistCorrections.anchor` finds which
        # item, and the classifier re-runs with the user's words as an
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
                room.room_id, event, anchor, event.event_id, ctx=ctx,
            ):
                return
        if await self._route_command(room, event, query, ctx):
            return
        await self._route_content(room, event, query, ctx, mentioned=mentioned)

    async def _route_command(self, room, event, query: str, ctx) -> bool:
        """Answer `help`, a scan's opener or closer, or `show <n>`.
        Returns whether the text was one of them."""
        query_lower = query.lower()
        reply_to = event.event_id
        if query_lower in HELP_COMMANDS:
            # Same per-room welcome the bot posted on first encounter
            # -- the user re-reads exactly what fits where they asked.
            await self._send(room.room_id, self._rooms.welcome_text_for(room, ctx), reply_to)
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
        else:
            return False
        return True

    async def _route_content(self, room, event, query: str, ctx, *, mentioned: bool) -> None:
        """File a link, search, keep a text, or say there is no filing here."""
        is_documents = self._rooms.is_documents_room(ctx)
        # Whether these words were spoken rather than typed. Never used to
        # route, only to decide whether the message was handed to us
        # deliberately.
        transcribed = voice.was_transcribed(event.source.get("content", {}))
        # With a scan open in this room, the person is handing things to
        # us: a line or a voice message between `(` and `)` is kept as a
        # note of its own, never dropped as chat, and the reply says the
        # scan waits. Search, links and mentions come first, as outside
        # a scan, so in the Documents Room a line stays a search.
        scanning = self._scan.is_open_in(event.sender, room.room_id)
        if _is_just_url(query):
            # URL semantics don't change with mention — a URL in the
            # docs room means "ingest into Paperless"; anywhere else
            # it's a bookmark capture for the sender's bucket.
            await self._route_link(room, event, query, None, ctx, is_documents=is_documents)

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
            await self._route_link(room, event, embedded_url, hint or None, ctx,
                                   is_documents=is_documents)

        elif mentioned or is_documents:
            # Free-text search runs whenever the user explicitly
            # addressed the bot (any room) or the message landed in
            # the documents room (where search is the default). A
            # short chat-shaped query like "Duff Insurance" is fine — that's
            # exactly the kind of thing recall is for.
            await self._search.search(room.room_id, query, event.event_id, sender=event.sender)

        elif scanning or transcribed or (
            self._looks_like_paste(query) and self._rooms.count_humans(room) < 2
        ):
            await self._keep_text(room, event, query, ctx, transcribed=transcribed)
            if scanning:
                await self._scan.say_still_open(room.room_id, event.event_id)

        elif self.get_thread_root(event) is not None:
            # A message in one of our threads (`_thread_is_ours` let it
            # through) is addressed to us, and nothing above could place
            # it: no filing of ours is readable there. Say so, because
            # silence reads as a correction that was taken.
            await self._answer(room.room_id, self.t("correction_no_filing"), event.event_id)

        else:
            # Chat, and nothing else matched. Either short (a capture
            # room's own threshold) or long in a room with other people
            # in it, where keeping something is 📌 rather than a guess.
            logger.debug(
                "[archivist] {} ignored text from {}: {!r}",
                room.room_id, event.sender, query[:60],
            )

    async def _route_link(
        self, room, event, url: str, hint: str | None, ctx, *, is_documents: bool,
    ) -> None:
        """A link: into Paperless in the documents room, a bookmark elsewhere."""
        if is_documents:
            await self._documents.file_url(
                room.room_id, url, event.event_id, ctx=ctx,
                date_filed=event_date(event),
                submitter_mxid=event.sender,
            )
        else:
            await self._captures.capture_url(
                room.room_id, url, event.sender, event.event_id, ctx=ctx,
                capture_id=event.event_id,
                user_hint=hint,
            )

    async def _keep_text(self, room, event, query: str, ctx, *, transcribed: bool) -> None:
        """Keep a paste or a voice memo as a note.

        One person in the room, paste-shaped message, no mention: file as
        text capture. Nobody is being talked *to* here, so a long message
        is material dropped for us to keep.

        Above one human the room is a conversation, and length stops
        meaning anything: people write long messages to each other all
        day and almost none of it is meant to be kept. Guessing there
        produced notes titled after somebody's error message. So the
        ambient path is off and 📌 is how you keep something -- explicit,
        visible in the timeline, and already the one trigger that ignores
        every room mode.

        Links and files are unaffected in either case: pasting a URL or
        dropping a PDF is a deliberate act, not a turn in a conversation.
        A voice memo is the same kind of act: nobody holds the mic button
        for thirty seconds as a turn in a chat, and the length heuristic
        that stands in for deliberateness elsewhere cannot read it. So
        speech carries itself past this gate, in any room, exactly as an
        upload does.

        This is not speech being routed differently. Everything above
        still applies to it unchanged -- a memo addressed to the bot is a
        question, in the documents room it is a search, in a thread it
        obeys ownership. Only "was this handed to us on purpose?" is
        answered another way, because for speech there is another way to
        answer it.
        """
        await self._captures.capture_text(
            room.room_id, query, event.sender, event.event_id, ctx=ctx,
            capture_id=event.event_id, transcribed=transcribed,
            sent_at=time_of_ms(getattr(event, "server_timestamp", 0)),
            caption=_attachment_caption(
                event.source.get("content", {}).get(voice.TRANSCRIPT_KEY) or {}
            ),
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





















