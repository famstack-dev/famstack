"""MicroBot — shared base class for all famstack Matrix bots.

A MicroBot handles everything a Matrix bot needs to exist: login, session
persistence across container restarts, E2E encryption, the sync loop, and
auto-accepting room invitations. Subclasses only implement the interesting
part — what to do when a message arrives.

The contract is simple:
  1. Subclass MicroBot
  2. Set `name = "mybot"` (used for session files and logging)
  3. Implement `register_callbacks(client)` to wire event handlers

The base class then handles:
  - Login with password, or restore a saved session (survives restarts)
  - E2E encryption setup (Element X forces encryption on DMs)
  - Auto-trust all device keys (family LAN — no verification needed)
  - Initial sync that skips old messages (bots don't replay history)
  - Auto-accept room invitations
  - The sync loop with error recovery
  - Message dedup via per-room cursor (at-most-once delivery)

Session persistence: each bot stores its access token and device ID in
a JSON file at {session_dir}/{name}.session.json. On restart, the bot
restores the session instead of creating a new login — this prevents
the "unknown device" problem where other clients can't encrypt for a
bot that logs in with a new device every time.

Message cursor: Matrix is used as a message bus — bots may be restarted
at any time (e.g. when a new stacklet is installed and core is refreshed).
Each bot keeps a per-room timestamp cursor on disk. Callbacks registered
via add_event_callback() only fire for messages newer than the cursor.
The cursor is advanced before the callback runs, so if the callback
triggers a container restart (stacker running "stack up"), the message
won't be replayed. This gives at-most-once delivery — safe for commands
and document processing where replay would cause duplicates.
"""

import asyncio
import json
from pathlib import Path

import markdown
from loguru import logger
from nio import (
    AsyncClient,
    AsyncClientConfig,
    InviteMemberEvent,
    LoginResponse,
    MegolmEvent,
)

from room_context import RoomContext, context_for


class MicroBot:
    """Base class for lightweight Matrix bots.

    Subclasses must set `name` and implement `register_callbacks()`.
    All bot-specific config from bot.toml [settings] arrives as kwargs
    and is stored in `self.config` for the subclass to read.
    """

    name: str = "bot"

    # Per-event handler timeout. Long enough for the slowest realistic
    # operation (LLM classify with vision attachments, Paperless upload,
    # git mirror push), short enough that a stuck handler doesn't keep
    # the typing indicator pinned forever or block subsequent events.
    # Subclasses can raise this by setting a class attribute on themselves.
    HANDLER_TIMEOUT_SECONDS: int = 180

    def __init__(self, homeserver: str, user_id: str, password: str, session_dir: str, **config):
        self.homeserver = homeserver
        self.user_id = user_id
        self.password = password
        self.config = config
        self._session_dir = Path(session_dir)
        self.session_file = self._session_dir / f"{self.name}.session.json"
        self._cursor_file = self._session_dir / f"{self.name}-cursor"
        self._cursors = self._load_cursors()
        self._client: AsyncClient | None = None
        self._running = False

    async def start(self) -> None:
        """Start the bot: login -> initial sync -> register callbacks -> sync loop."""
        store_path = str(self._session_dir / f"{self.name}_crypto")
        Path(store_path).mkdir(parents=True, exist_ok=True)

        config = AsyncClientConfig(store_sync_tokens=True, encryption_enabled=True)
        self._client = AsyncClient(
            self.homeserver, self.user_id,
            store_path=store_path, config=config,
        )
        self._running = True

        # ── Login or restore session ─────────────────────────────────
        logged_in = False
        if self._restore_session():
            try:
                resp = await self._client.whoami()
                if hasattr(resp, "user_id") and resp.user_id == self.user_id:
                    logger.info("[{}] Session valid for {}", self.name, resp.user_id)
                    logged_in = True
                else:
                    logger.warning("[{}] Session invalid (whoami returned {}), clearing", self.name, type(resp).__name__)
                    self._clear_session()
            except Exception as e:
                logger.warning("[{}] Session check failed: {}, clearing", self.name, e)
                self._clear_session()

        if not logged_in:
            logged_in = await self._password_login()

        if not logged_in:
            logger.error("[{}] Cannot authenticate — giving up", self.name)
            await self._client.close()
            return

        # ── E2E encryption ───────────────────────────────────────────
        if self._client.olm:
            resp = await self._client.keys_upload()
            logger.info("[{}] Keys uploaded: {}", self.name, type(resp).__name__)

        # ── Auto-accept invitations ──────────────────────────────────
        async def on_invite(room, event):
            if isinstance(event, InviteMemberEvent) and event.state_key == self.user_id:
                logger.info("[{}] Invited to {} by {}", self.name, room.room_id, event.sender)
                resp = await self._client.join(room.room_id)
                logger.info("[{}] Join result: {}", self.name, resp)

        self._client.add_event_callback(on_invite, InviteMemberEvent)

        # ── Initial sync ─────────────────────────────────────────────
        logger.info("[{}] Initial sync...", self.name)
        await self._client.sync(timeout=10000, full_state=True)

        self._trust_all_devices()

        rooms = self._client.rooms
        logger.info("[{}] In {} room(s): {}", self.name, len(rooms), list(rooms.keys()))

        # ── Undecryptable message handler ────────────────────────────
        async def on_encrypted(room, event):
            if isinstance(event, MegolmEvent) and event.sender != self.user_id:
                logger.warning(
                    "[{}] Could not decrypt event in {} from {} (algorithm={})",
                    self.name, room.room_id, event.sender,
                    getattr(event, "algorithm", "?"),
                )
                await self._client.room_send(
                    room_id=room.room_id,
                    message_type="m.room.message",
                    content={
                        "msgtype": "m.notice",
                        "body": "I couldn't decrypt that message. "
                                "Try re-inviting me to this room, or send "
                                "from a verified session.",
                    },
                )

        self._client.add_event_callback(on_encrypted, MegolmEvent)

        # ── First-sync hook ────────────────────────────────────────
        welcome_marker = self._session_dir / f"{self.name}.welcomed"
        if not welcome_marker.exists():
            try:
                await self.on_first_sync()
                welcome_marker.touch()
            except Exception as e:
                logger.debug("[{}] on_first_sync: {}", self.name, e)

        # ── Subclass callbacks ───────────────────────────────────────
        self.register_callbacks(self._client)

        # ── Sync loop ────────────────────────────────────────────────
        logger.info("[{}] Running", self.name)
        while self._running:
            try:
                await self._client.sync(timeout=30000)
                self._trust_all_devices()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("[{}] Sync error: {}", self.name, e)
                if self._running:
                    await asyncio.sleep(5)

        await self._client.close()
        logger.info("[{}] Stopped", self.name)

    async def _password_login(self, retries=30, interval=10) -> bool:
        """Log in with password, retrying until the account exists.

        On a fresh install, the bot runner starts before messages creates
        the bot accounts. Instead of crashing, we wait.
        """
        if not self.password:
            logger.error("[{}] No password available", self.name)
            return False

        for attempt in range(1, retries + 1):
            resp = await self._client.login(self.password)
            if isinstance(resp, LoginResponse):
                logger.info("[{}] Logged in (device {})", self.name, resp.device_id)
                self._save_session()
                return True

            if attempt == 1:
                logger.info("[{}] Login not ready, waiting for account creation...", self.name)
            elif attempt % 6 == 0:
                logger.info("[{}] Still waiting... (attempt {})", self.name, attempt)

            await asyncio.sleep(interval)

        logger.error("[{}] Login failed after {} attempts", self.name, retries)
        return False

    def register_callbacks(self, client: AsyncClient) -> None:
        """Override in subclass to register event callbacks.

        Use self.add_event_callback() instead of client.add_event_callback()
        so the framework filters out already-processed messages.
        """
        raise NotImplementedError

    def add_event_callback(self, callback, event_type):
        """Register an event callback with the standard framework wrap.

        The wrap does three things every bot wants:

          1. **Dedup** — only fires for messages newer than the per-room
             cursor; the cursor advances before the callback runs, so a
             callback that triggers a restart can't replay the message.
             At-most-once delivery.
          2. **Typing indicator** — shows that the bot is working from
             the moment the cursor advances until the handler returns
             (or fails). This is the family's only signal that the bot
             saw the message at all.
          3. **Timeout + error response** — runs the callback under
             ``HANDLER_TIMEOUT_SECONDS`` and, on timeout *or* any
             unhandled exception, posts a user-facing notice into the
             room via ``_send_error``. A silently-failing bot is worse
             than a bot that says "sorry, try again."

        Handlers don't need their own try/except or typing management;
        the framework owns both. Subclasses customize the error wording
        by overriding ``_format_handler_error``.
        """
        async def wrapper(room, event):
            if event.sender == self.user_id:
                return
            ts = getattr(event, "server_timestamp", 0)
            if ts <= self._cursors.get(room.room_id, 0):
                return
            self._advance_cursor(room.room_id, ts)

            # From here on, the bot owns this event. The framework
            # guarantees a typing-off in `finally` (and posts an error
            # notice if the handler raises or runs over budget), but
            # it does NOT auto-set typing-on. Handlers control when the
            # indicator appears, because the right moment depends on
            # whether they post an intermediate confirmation message
            # first (which clears the indicator on Element's side).
            try:
                await asyncio.wait_for(
                    callback(room, event),
                    timeout=self.HANDLER_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as e:
                logger.error(
                    "[{}] handler timed out after {}s in {}",
                    self.name, self.HANDLER_TIMEOUT_SECONDS, room.room_id,
                )
                await self._send_error(room.room_id, event, e)
            except Exception as e:
                logger.error(
                    "[{}] handler error in {}: {}",
                    self.name, room.room_id, e, exc_info=True,
                )
                await self._send_error(room.room_id, event, e)
            finally:
                await self._set_typing(room.room_id, on=False)

        self._client.add_event_callback(wrapper, event_type)

    # ── Typing + error response ──────────────────────────────────────────
    #
    # The framework owns the "bot is working" signal. Handlers that used
    # to call `_set_typing` themselves can drop those calls — the wrap in
    # `add_event_callback` handles it.

    async def _set_typing(self, room_id: str, on: bool = True) -> None:
        """Toggle the bot's typing indicator in a room.

        Best-effort: a typing call that fails (room not joined yet,
        homeserver hiccup) shouldn't crash the message handler. The
        300000ms (5 min) timeout matches what the archivist used before
        this moved into the framework.
        """
        logger.info("[{}] typing -> {} in {}", self.name, "on" if on else "off", room_id)
        try:
            resp = await self._client.room_typing(
                room_id, typing_state=on, timeout=300000,
            )
            logger.info("[{}] typing response: {}", self.name, type(resp).__name__)
        except Exception as e:
            logger.warning("[{}] typing toggle failed: {}", self.name, e)

    async def _room_send(
        self,
        room_id: str,
        content: dict,
        message_type: str = "m.room.message",
    ) -> None:
        """Thin wrapper around ``self._client.room_send``.

        Exists so subclasses can route every send through a single
        framework-owned method — useful when we want to add cross-
        cutting behavior (audit logging, retries, etc.) without
        touching every call site. Today it's a passthrough.
        """
        await self._client.room_send(
            room_id=room_id, message_type=message_type, content=content,
        )

    async def _send(
        self, room_id: str, text: str, reply_to: str | None = None,
        *, metadata: dict | None = None,
    ) -> None:
        """Send a formatted ``m.room.message``: markdown body + HTML.

        The single formatted-reply path for every bot. ``text`` is sent
        verbatim as the plaintext ``body`` and rendered to
        ``formatted_body`` for rich clients (tables + fenced code
        enabled). ``reply_to`` threads the message under a prior event;
        ``metadata`` merges extra top-level keys into the content dict.

        Matrix content is a JSON object, so custom keys (e.g.
        ``dev.famstack.event``) are invisible to clients but readable by
        the bot when it fetches the event back — that's how a filing
        notification carries its structured envelope on the same visible
        message.

        Routes through ``_room_send`` so the typing indicator refreshes
        after every send: Matrix clients clear the indicator when they
        see a new bot message, so a long handler that posts an
        intermediate status would otherwise run silently afterwards.
        """
        html = markdown.markdown(text, extensions=["tables", "fenced_code"])
        content: dict = {
            "msgtype": "m.text",
            "body": text,
            "format": "org.matrix.custom.html",
            "formatted_body": html,
        }
        if reply_to:
            content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to}}
        if metadata:
            content.update(metadata)
        await self._room_send(room_id, content)

    # The famstack event envelope rides as a custom key on the visible
    # m.room.message (see `_send`'s `metadata`), so a filing is a single
    # replayable timeline event. Distinct from `emit_event`, which posts
    # a *separate* custom-typed event for bot-to-bot signalling.
    FAMSTACK_EVENT_KEY = "dev.famstack.event"

    async def _reply_parent_envelope(self, room_id: str, event) -> dict | None:
        """Return the famstack envelope on the message ``event`` replies to.

        When a user replies to one of the bot's own messages, fetch the
        parent from the homeserver and hand back its
        ``dev.famstack.event`` content (Matrix is the ledger — no local
        cache). The caller decides what the envelope *means*.

        Returns None when the event isn't a reply, the parent fetch
        fails, the parent isn't ours, or it carries no envelope. The
        ownership check matters: a reply to another user's message that
        happens to carry a matching key must not fire.
        """
        in_reply_to = (
            getattr(event, "source", {})
            .get("content", {})
            .get("m.relates_to", {})
            .get("m.in_reply_to", {})
            .get("event_id")
        )
        if not in_reply_to:
            return None
        try:
            resp = await self._client.room_get_event(room_id, in_reply_to)
        except Exception as e:
            logger.debug("[{}] reply parent fetch failed: {}", self.name, e)
            return None
        parent = getattr(resp, "event", None)
        if parent is None:
            return None
        if getattr(parent, "sender", None) != self.user_id:
            return None
        envelope = parent.source.get("content", {}).get(self.FAMSTACK_EVENT_KEY)
        return envelope if isinstance(envelope, dict) else None

    def _format_handler_error(self, event, exc: BaseException) -> str:
        """Render an exception as a user-facing message.

        Default is plain English so every bot has *something* sensible
        out of the box. Subclasses override to localize or to map
        specific exception types to kinder messages (e.g. "the model
        is asleep, try again in a minute").
        """
        if isinstance(exc, asyncio.TimeoutError):
            return "Sorry — that took longer than I'm willing to wait. Try again?"
        return "Sorry — something went wrong handling that message."

    async def _send_error(self, room_id: str, event, exc: BaseException) -> None:
        """Post a user-facing error message into the room.

        Replies to the original event so the user can see which message
        triggered the failure. Best-effort — if the send itself fails
        we log at warning and stop, no recursion. Uses ``m.notice`` so
        Element renders it with the bot-message styling rather than as
        a regular chat line.
        """
        try:
            text = self._format_handler_error(event, exc)
            content = {"msgtype": "m.notice", "body": text}
            reply_to = getattr(event, "event_id", None)
            if reply_to:
                content["m.relates_to"] = {
                    "m.in_reply_to": {"event_id": reply_to},
                }
            await self._client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content=content,
            )
        except Exception as e:
            logger.warning(
                "[{}] error-response send failed in {}: {}",
                self.name, room_id, e,
            )

    async def on_first_sync(self) -> None:
        """Called once after the very first sync. Override to send welcome
        messages, announce the bot to rooms, etc. Not called on restarts."""
        pass

    # ── Per-event routing primitives ─────────────────────────────────────
    #
    # Every bot eventually needs the same three pieces of context per event:
    # which room is this, was the bot addressed, and should the bot react
    # at all? Live in the framework so subclasses don't reinvent them.
    #
    # The model is intentionally pure / side-effect-free. Each method
    # consumes the nio room/event and returns a value object or bool —
    # no Matrix client state read beyond what's already in memory, no
    # config persistence. Subclasses override the small seam
    # ``_room_mode_allows_react`` when they want per-room config.

    def _room_context(self, room) -> RoomContext:
        """Snapshot of the room behind the current event.

        Cheap (a handful of attribute reads). The frozen dataclass keeps
        handlers honest — once routing has been decided, the same view
        of the room flows through the rest of the request without any
        risk of mid-flight mutation.
        """
        return context_for(room, bot_user_id=self.user_id)

    def _is_bot_mentioned(self, event) -> bool:
        """True if the event explicitly addresses this bot.

        Two paths, OR-ed:

          1. ``m.mentions.user_ids`` (MSC3952) — modern Matrix clients
             (Element X, Element-web) populate this list when the user
             tab-completes a mention. This is the authoritative signal:
             the user *meant* to ping the bot.
          2. Mxid substring in the body — for clients that don't emit
             ``m.mentions`` and for messages that pasted the full
             ``@bot:server`` form. The check is on the full mxid, not
             the localpart, so a casual mention of the bot's name in
             chat doesn't trip the gate.
        """
        content = (
            event.source.get("content", {}) if hasattr(event, "source") else {}
        )
        mentions = content.get("m.mentions") or {}
        if self.user_id in (mentions.get("user_ids") or []):
            return True
        body = getattr(event, "body", "") or ""
        return self.user_id in body

    def _should_react(self, ctx: RoomContext, *, mentioned: bool) -> bool:
        """Decide whether the bot acts on the current event at all.

        Two cases are never gated — the bot always reacts:

          * ``mentioned`` is True. An explicit @-tag is an unambiguous
            address; ignoring it would be confusing regardless of any
            room-mode config.
          * ``ctx.is_dm`` is True. A private chat with one human is
            unambiguous by construction — there's nobody else for the
            message to be aimed at.

        Everything else (group rooms with 3+ members, no mention) is
        subject to ``_room_mode_allows_react`` — the single seam a
        subclass overrides when it wants per-room mode config. The
        framework default lets every event through.
        """
        if mentioned:
            return True
        if ctx.is_dm:
            return True
        return self._room_mode_allows_react(ctx)

    def _room_mode_allows_react(self, ctx: RoomContext) -> bool:
        """The configurable branch of ``_should_react``.

        Group-room behavior is the only thing rooms might want to gate.
        Anticipated shape — once a subclass / config lands:

            [room_modes]
            "#family:home.local"  = "mention"  # only when @-tagged
            "#friends:home.local" = "off"      # ignore entirely
            "#open-chat:home"     = "always"   # current default

        With the two always-on cases handled upstream, "mention" mode
        collapses to "ignore" here (mentions never reach this branch).
        The framework default — react in every group room — is the
        least-surprise baseline; bots that want to be quieter override
        this method.
        """
        del ctx
        return True

    @staticmethod
    def strip_mention(body: str, bot_user_id: str) -> str:
        """Remove the bot's mxid (and any clinging punctuation/whitespace)
        from a message body.

        Used after ``_is_bot_mentioned`` to recover the *actual* query
        the user typed: ``"@archivist-bot:home.local Pollos?"`` → ``"Pollos?"``.
        Idempotent: callers can apply it unconditionally; if the mxid
        isn't present, the body is returned unchanged.

        Conservative on punctuation: trims a single trailing ``:`` or
        ``,`` after the mxid (common in tab-complete output like
        ``"@bot: do the thing"``) and collapses the surrounding
        whitespace, nothing more.
        """
        if not body or bot_user_id not in body:
            return body
        cleaned = body.replace(bot_user_id, " ", 1)
        # Common tab-complete suffixes ("@bot: ", "@bot, ").
        cleaned = cleaned.replace(" : ", " ").replace(" , ", " ")
        return " ".join(cleaned.split()).strip()

    async def emit_event(self, room_id: str, event_type: str, body: dict) -> bool:
        """Emit a structured (non-message) event into a Matrix room.

        Custom event types (convention: dev.famstack.<name>) form the
        framework's bus for bot-to-bot communication. Element ignores
        unknown types, so they stay invisible in the chat UI while other
        bots filter on them to drive downstream behavior.

        Returns True on success, False on failure — failures are logged
        but not raised, because the event bus is best-effort: a missed
        event shouldn't take down the caller's main path.
        """
        try:
            await self._client.room_send(
                room_id=room_id,
                message_type=event_type,
                content=body,
            )
            return True
        except Exception as e:
            logger.warning("[{}] Failed to emit {} to {}: {}", self.name, event_type, room_id, e)
            return False

    async def stop(self) -> None:
        """Signal the sync loop to exit."""
        self._running = False

    def _trust_all_devices(self) -> None:
        """Mark all known devices as trusted without interactive verification."""
        if not self._client.olm:
            return
        try:
            for user_id in self._client.device_store.users:
                for device in self._client.device_store.active_user_devices(user_id):
                    if not self._client.olm.is_device_verified(device):
                        self._client.verify_device(device)
                        logger.info("[{}] Trusted device {} of {}", self.name, device.device_id, user_id)
        except Exception as e:
            logger.debug("[{}] Trust devices: {}", self.name, e)

    def _restore_session(self) -> bool:
        """Restore a saved Matrix session."""
        if not self.session_file.exists():
            return False
        try:
            data = json.loads(self.session_file.read_text())
            self._client.access_token = data["access_token"]
            self._client.user_id = data["user_id"]
            self._client.device_id = data["device_id"]
            logger.info("[{}] Restored session (device {})", self.name, data["device_id"])
            return True
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("[{}] Bad session file: {}", self.name, e)
            return False

    def _clear_session(self):
        """Wipe saved session and in-memory credentials."""
        self._client.access_token = ""
        self._client.device_id = ""
        if self.session_file.exists():
            self.session_file.unlink()
            logger.info("[{}] Deleted stale session file", self.name)

    def _save_session(self) -> None:
        """Persist the current session so it survives container restarts."""
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text(json.dumps({
            "access_token": self._client.access_token,
            "user_id": self._client.user_id,
            "device_id": self._client.device_id,
        }))

    # ── Message cursor ───────────────────────────────────────────────
    # Per-room timestamp of the last processed message. Callbacks
    # registered via add_event_callback() only see newer messages.

    def _load_cursors(self):
        try:
            return json.loads(self._cursor_file.read_text())
        except Exception:
            return {}

    def _advance_cursor(self, room_id, server_timestamp):
        self._cursors[room_id] = server_timestamp
        self._cursor_file.write_text(json.dumps(self._cursors))
