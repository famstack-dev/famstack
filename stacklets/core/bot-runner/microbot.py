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
  - Reliable, burst-safe delivery by draining the room timeline

Session persistence: each bot stores its access token and device ID in
a JSON file at {session_dir}/{name}.session.json. On restart, the bot
restores the session instead of creating a new login — this prevents
the "unknown device" problem where other clients can't encrypt for a
bot that logs in with a new device every time.

Delivery model — Synapse is the queue: the room timeline is a durable,
ordered log; we treat it as the work queue rather than maintaining a
separate one. The live `sync()` is only a doorbell — it keeps the bot
present and tells us the room advanced. The actual work is read by
`_drain()`, which pages the timeline forward from a durable per-room
cursor via `room_messages` and dispatches each event to its handler in
order. Reading the timeline directly (instead of relying on the live
sync payload) means a burst can never be lost to a "limited" (gappy)
sync — every event since the cursor is still on the timeline.

The cursor (a per-room server_timestamp on disk) advances only *after*
a handler returns, so a crash mid-processing replays the event rather
than skipping it: at-least-once delivery. Handlers must therefore be
idempotent — keyed on a stable id, every write an upsert — so a replay
is a no-op. The timestamp cursor has a rare same-millisecond edge,
accepted for now.

The bot also sets the public `m.read` receipt to each processed event,
so the room shows its "Seen by" progress in Element. That is purely
cosmetic — the local cursor is the source of truth, so a failed receipt
never affects what gets processed. (Using the Matrix `m.fully_read`
marker *as* the cursor was tried and dropped: the account-data callback
that reads it back didn't fire reliably, so the local cursor stays.)
"""

import asyncio
import json
import time
from pathlib import Path

import aiohttp
import markdown
from loguru import logger
from nio import (
    AsyncClient,
    AsyncClientConfig,
    InviteMemberEvent,
    JoinResponse,
    LoginResponse,
    MegolmEvent,
    MessageDirection,
    RoomMessagesResponse,
    SyncResponse,
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
        # The display name from bot.toml, applied to the Matrix profile on
        # launch (the runner sets it post-construction). bot.toml is
        # authoritative; account setup only runs for new bots, so this is how a
        # rename reaches an already-provisioned account.
        self.display_name: str | None = config.get("display_name")
        self._session_dir = Path(session_dir)
        self.session_file = self._session_dir / f"{self.name}.session.json"
        self._cursor_file = self._session_dir / f"{self.name}-cursor"
        self._cursors = self._load_cursors()
        # (event_type_or_tuple, wrapped_handler) pairs. The drain dispatches
        # to these; we do not register them with nio, because the live sync
        # is only a doorbell — delivery happens in `_drain`.
        self._handlers: list[tuple] = []
        # Rooms we have auto-accepted but whose state hasn't yet arrived
        # via sync. The SyncResponse callback drains this set and fires
        # `on_room_joined` once nio has populated the room.
        self._pending_room_joins: set[str] = set()
        self._client: AsyncClient | None = None
        # Lazily-created shared aiohttp session for non-nio HTTP (media
        # download, and subclasses' own API calls). Owned by the
        # framework so its lifecycle is tied to start()/teardown.
        self._http: aiohttp.ClientSession | None = None
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
            await self._aclose()
            return

        # ── E2E encryption ───────────────────────────────────────────
        if self._client.olm:
            resp = await self._client.keys_upload()
            logger.info("[{}] Keys uploaded: {}", self.name, type(resp).__name__)

        # ── Auto-accept invitations ──────────────────────────────────
        # Two-stage hand-off so subclass `on_room_joined` sees a fully
        # populated room: invite-accept queues the room id, and the
        # sync loop fires `on_room_joined` on the first sync that
        # contains that room's state. The hand-off is driven from the
        # sync loop directly — NOT via `add_response_callback` — since
        # nio only dispatches response callbacks inside
        # `sync_forever()`, which this runner does not use.

        async def on_invite(room, event):
            if isinstance(event, InviteMemberEvent) and event.state_key == self.user_id:
                logger.info("[{}] Invited to {} by {}", self.name, room.room_id, event.sender)
                resp = await self._client.join(room.room_id)
                logger.info("[{}] Join result: {}", self.name, resp)
                if isinstance(resp, JoinResponse):
                    self._pending_room_joins.add(room.room_id)

        self._client.add_event_callback(on_invite, InviteMemberEvent)

        # ── Initial sync ─────────────────────────────────────────────
        logger.info("[{}] Initial sync...", self.name)
        await self._client.sync(timeout=10000, full_state=True)

        self._trust_all_devices()

        rooms = self._client.rooms
        logger.info("[{}] In {} room(s): {}", self.name, len(rooms), list(rooms.keys()))

        await self._sync_display_name()

        # ── Undecryptable message handler ────────────────────────────
        # By design: the bots do not read encrypted rooms. Tell the user
        # once per room (per process) what is going on and how to fix
        # it, instead of a cryptic decrypt error on every message.
        decrypt_notified: set[str] = set()
        DOCS_URL = (
            "https://github.com/famstack-dev/famstack/blob/main/"
            "docs/user-guide.md#save-anything-capture-rooms"
        )

        async def on_encrypted(room, event):
            if isinstance(event, MegolmEvent) and event.sender != self.user_id:
                logger.warning(
                    "[{}] Could not decrypt event in {} from {} (algorithm={})",
                    self.name, room.room_id, event.sender,
                    getattr(event, "algorithm", "?"),
                )
                if room.room_id in decrypt_notified:
                    return
                decrypt_notified.add(room.room_id)
                await self._client.room_send(
                    room_id=room.room_id,
                    message_type="m.room.message",
                    content={
                        "msgtype": "m.notice",
                        "body": (
                            "This room is encrypted, and I can't read "
                            "encrypted messages. That's by design: anything "
                            "sent encrypted stays private and is never "
                            "processed by famstack.\n\n"
                            "To work with me, create a new room with "
                            "encryption disabled (encryption can't be "
                            "switched off later). Background: " + DOCS_URL
                        ),
                        "format": "org.matrix.custom.html",
                        "formatted_body": (
                            "This room is encrypted, and I can't read "
                            "encrypted messages. That's <b>by design</b>: "
                            "anything sent encrypted stays private and is "
                            "never processed by famstack.<br/><br/>"
                            "To work with me, create a new room with "
                            "<b>encryption disabled</b> (encryption can't be "
                            "switched off later). "
                            f'<a href="{DOCS_URL}">Background</a>.'
                        ),
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

        # ── Sync + drain loop ─────────────────────────────────────────
        # `sync()` is the doorbell (long-poll: returns on activity or after
        # the timeout); `_drain()` does the work, reading the timeline from
        # the durable cursor so nothing is lost to a limited sync.
        logger.info("[{}] Running", self.name)
        while self._running:
            try:
                resp = await self._client.sync(timeout=30000)
                self._trust_all_devices()
                # Bare `sync()` does NOT dispatch nio response callbacks
                # (only `sync_forever()` does), so the pending-joins
                # hand-off must be driven from here explicitly.
                await self._on_sync_for_pending_joins(resp)
                await self._drain()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("[{}] Sync error: {}", self.name, e)
                if self._running:
                    await asyncio.sleep(5)

        await self._aclose()
        logger.info("[{}] Stopped", self.name)

    async def _aclose(self) -> None:
        """Close framework-owned network resources. Idempotent — safe on
        both the login-failure path and normal shutdown, and when a
        subclass never opened the http session."""
        if self._client is not None:
            await self._client.close()
        if self._http is not None:
            await self._http.close()

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

        The wrap does two things every bot wants:

          1. **Typing indicator** — a typing-off is guaranteed in
             `finally`. It does NOT auto-set typing-on; handlers control
             when the indicator appears, because the right moment depends
             on whether they post an intermediate confirmation first
             (which clears the indicator on Element's side).
          2. **Timeout + error response** — runs the callback under
             ``HANDLER_TIMEOUT_SECONDS`` and, on timeout *or* any
             unhandled exception, posts a user-facing notice into the
             room via ``_send_error``. A silently-failing bot is worse
             than a bot that says "sorry, try again."

        The handler is not registered with nio; it is stored and invoked
        by `_drain` in timeline order. Dedup and cursor advancement live
        there — the drain only dispatches events past the cursor and
        advances it *after* the handler returns (at-least-once), so
        handlers must be idempotent. Subclasses customize the error
        wording by overriding ``_format_handler_error``.
        """
        async def wrapper(room, event):
            if event.sender == self.user_id:
                return
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

        # Stored for the drain, not registered with nio: the live sync is
        # only a doorbell, delivery happens in `_drain`.
        self._handlers.append((event_type, wrapper))

    # ── Drain: the timeline is the queue ──────────────────────────────────
    #
    # Each cycle we read everything past the per-room cursor straight off
    # the timeline via `room_messages` and dispatch it in order. Reading the
    # timeline (rather than trusting the live sync payload) is gap-free: a
    # burst that outran a "limited" sync is still fully on the timeline, so
    # nothing is dropped under load.

    DRAIN_PAGE_SIZE = 50
    MAX_DRAIN_PAGES = 40  # safety cap: up to ~2000 backlogged events per room

    async def _drain(self) -> None:
        """Process new events in every joined room, in timeline order."""
        if not self._handlers or self._client is None:
            return
        for room_id in list(self._client.rooms.keys()):
            try:
                await self._drain_room(room_id)
            except Exception as e:
                logger.warning("[{}] drain error in {}: {}", self.name, room_id, e)

    async def _drain_room(self, room_id: str) -> None:
        # First sight of a room: anchor the cursor at "now" so we don't
        # replay its whole history (the old "skip old messages" behavior).
        if room_id not in self._cursors:
            self._advance_cursor(room_id, int(time.time() * 1000))
            return

        cursor = self._cursors[room_id]

        # Page backward from the live sync position, collecting events newer
        # than the cursor, until we cross it. Then process oldest-first.
        pending: list = []
        start = self._client.next_batch
        for _ in range(self.MAX_DRAIN_PAGES):
            resp = await self._client.room_messages(
                room_id, start=start, direction=MessageDirection.back,
                limit=self.DRAIN_PAGE_SIZE,
            )
            if not isinstance(resp, RoomMessagesResponse) or not resp.chunk:
                break
            crossed = False
            for event in resp.chunk:  # newest -> oldest
                if getattr(event, "server_timestamp", 0) <= cursor:
                    crossed = True
                    break
                pending.append(event)
            if crossed or not resp.end or resp.end == start:
                break
            start = resp.end

        for event in sorted(pending, key=lambda e: getattr(e, "server_timestamp", 0)):
            await self._dispatch(room_id, event)
            # Advance only after the handler returns: a crash mid-handler
            # replays the event (at-least-once), it is never skipped.
            self._advance_cursor(room_id, getattr(event, "server_timestamp", cursor))
            # Move the public read receipt too, so the room shows the bot's
            # "Seen by" progress. Purely cosmetic — the cursor above is the
            # source of truth, so a failed receipt never affects delivery.
            await self._set_read_receipt(room_id, getattr(event, "event_id", None))

    async def ack_event(self, room_id: str, event) -> None:
        """Durably advance the cursor to `event` BEFORE its handler returns.

        Opt-in escape hatch from the drain's at-least-once contract, for
        handlers whose side effects must run at most once. The concrete
        case: stacker commands recreate this very container (`stack up`
        ends in a core refresh), so the handler can never return and
        advance-after would replay the command on every restart — a
        self-sustaining loop. Acking first trades a possibly-lost "Done"
        reply for never re-executing.

        Capture/document handlers must NOT call this: their replays are
        deliberate (61d2093) and dedup'd downstream.
        """
        ts = getattr(event, "server_timestamp", 0)
        if ts > self._cursors.get(room_id, 0):
            self._advance_cursor(room_id, ts)
        await self._set_read_receipt(room_id, getattr(event, "event_id", None))

    async def _set_read_receipt(self, room_id: str, event_id: str | None) -> None:
        """Best-effort public m.read receipt for the "Seen by" indicator."""
        if not event_id:
            return
        try:
            await self._client.update_receipt_marker(room_id, event_id)
        except Exception as e:
            logger.debug("[{}] read receipt update failed in {}: {}",
                         self.name, room_id, e)

    async def _dispatch(self, room_id: str, event) -> None:
        """Invoke every handler whose registered type matches the event."""
        room = self._client.rooms.get(room_id)
        if room is None:
            return
        for event_type, handler in self._handlers:
            if isinstance(event, event_type):
                await handler(room, event)

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
        *, metadata: dict | None = None, thread_root_event_id: str | None = None,
        line_breaks: bool = False,
    ) -> None:
        """Send a formatted ``m.room.message``: markdown body + HTML.

        The single formatted-reply path for every bot. ``text`` is sent
        verbatim as the plaintext ``body`` and rendered to
        ``formatted_body`` for rich clients (tables + fenced code
        enabled). ``reply_to`` quotes a prior event; ``thread_root_event_id``
        posts it as an ``m.thread`` reply under that root (e.g. an email's
        full body under its card); ``metadata`` merges extra top-level keys
        into the content dict.

        Matrix content is a JSON object, so custom keys (e.g.
        ``dev.famstack.event``) are invisible to clients but readable by
        the bot when it fetches the event back — that's how a filing
        notification carries its structured envelope on the same visible
        message.

        Routes through ``_room_send`` so the typing indicator refreshes
        after every send: Matrix clients clear the indicator when they
        see a new bot message, so a long handler that posts an
        intermediate status would otherwise run silently afterwards.

        ``line_breaks`` adds the ``nl2br`` markdown extension so every newline
        becomes a ``<br>`` — chat behaviour (Slack/WhatsApp), needed for a
        pasted email body where single newlines would otherwise collapse.
        """
        exts = ["tables", "fenced_code"] + (["nl2br"] if line_breaks else [])
        html = markdown.markdown(text, extensions=exts)
        content: dict = {
            "msgtype": "m.text",
            "body": text,
            "format": "org.matrix.custom.html",
            "formatted_body": html,
        }
        if thread_root_event_id:
            content["m.relates_to"] = {
                "rel_type": "m.thread",
                "event_id": thread_root_event_id,
                "is_falling_back": True,
                "m.in_reply_to": {"event_id": reply_to or thread_root_event_id},
            }
        elif reply_to:
            content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to}}
        if metadata:
            content.update(metadata)
        await self._room_send(room_id, content)

    # The famstack event envelope rides as a custom key on the visible
    # m.room.message (see `_send`'s `metadata`), so a filing is a single
    # replayable timeline event. Distinct from `emit_event`, which posts
    # a *separate* custom-typed event for bot-to-bot signalling.
    FAMSTACK_EVENT_KEY = "dev.famstack.event"

    # The raw-ingest block on an inbound message: the verbatim original a
    # source posts before classification, distinct from the post-classify
    # `dev.famstack.event` filing envelope above.
    SOURCE_KEY = "dev.famstack.source"

    # Marks a media event as bot-posted on behalf of a source (an email
    # attachment today): tells the archivist not to attribute the bot as a
    # person and carries provenance for the capture's tags.
    ATTACHMENT_KEY = "dev.famstack.attachment"

    @staticmethod
    def is_bot_user(user_id: str) -> bool:
        """Whether a Matrix user is a famstack bot, by convention.

        Bot accounts have a localpart ending in ``-bot`` (mail-bot,
        archivist-bot, scribe-bot, …). The framework owns this one
        definition so every surface agrees on it — counting the humans in
        a room (scope/visibility), ignoring bot-to-bot chatter, deciding
        on-behalf-of attribution. A non-bot string is simply not a bot.
        """
        return (user_id or "").split(":")[0].lstrip("@").endswith("-bot")

    async def post_source_message(
        self,
        room_id: str,
        *,
        body: str,
        source: str,
        raw_content: str,
        fields: dict | None = None,
        thread_root_event_id: str | None = None,
    ) -> str | None:
        """Post a twofold ingest message and return its event id.

        Two faces on one timeline event: a human-readable rendered ``body``
        (markdown + HTML for rich clients) and a machine ``dev.famstack.source``
        block carrying the verbatim ``raw_content`` plus per-source ``fields``
        (an email's from / message_id / thread_root, a future source's own
        descriptors). ``raw_content`` is the reproducibility anchor (ADR-010):
        re-deriving the vault entry reads it, never the upstream server.

        When ``thread_root_event_id`` is given the message is posted as an
        ``m.thread`` reply under that root (with a reply fallback so
        non-threaded clients still show it in context), so an email
        conversation maps onto one Matrix thread. Returns the new event's id
        — the caller uses the first message's id as the thread root for the
        rest — or None on failure.

        Framework plumbing: any ingest source posts through here and gets the
        twofold + threaded shape without reimplementing the wire format.
        """
        source_block: dict = {"source": source, "raw_content": raw_content}
        if fields:
            source_block.update(fields)

        html = markdown.markdown(body, extensions=["tables", "fenced_code"])
        content: dict = {
            "msgtype": "m.text",
            "body": body,
            "format": "org.matrix.custom.html",
            "formatted_body": html,
            self.SOURCE_KEY: source_block,
        }
        if thread_root_event_id:
            content["m.relates_to"] = {
                "rel_type": "m.thread",
                "event_id": thread_root_event_id,
                "is_falling_back": True,
                "m.in_reply_to": {"event_id": thread_root_event_id},
            }
        try:
            resp = await self._client.room_send(
                room_id=room_id, message_type="m.room.message", content=content,
            )
        except Exception as e:
            logger.warning("[{}] post_source_message failed: {}", self.name, e)
            return None
        return getattr(resp, "event_id", None)

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

    def _ensure_http(self) -> aiohttp.ClientSession:
        """The shared aiohttp session, created on first use.

        Subclasses that make their own HTTP calls (Paperless, OpenAI,
        Forgejo) reuse this one session instead of spinning up their
        own, so there's a single pool with a single framework-owned
        lifecycle. Closed in `_aclose` on shutdown.

        The explicit timeout is load-bearing: aiohttp's default is
        300s total, and one black-holed request (e.g. a host port that
        accepts the connection and never answers) blocks the bot's
        whole event loop for five minutes of total silence. Bound it
        so a hang surfaces as a fast, named ClientError instead.
        """
        if self._http is None:
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60, connect=10),
            )
        return self._http

    async def _sync_display_name(self) -> None:
        """Make the Matrix profile match the configured display name.

        bot.toml's `name` is authoritative. Account provisioning only runs for
        bots without a session, so a rename would otherwise never reach an
        already-created account — this applies it on every launch. Best-effort:
        only writes when it differs, and a profile error is logged, not fatal.
        """
        if not self.display_name:
            return
        try:
            resp = await self._client.get_displayname(self.user_id)
            current = getattr(resp, "displayname", None)
            if current != self.display_name:
                await self._client.set_displayname(self.display_name)
                logger.info(
                    "[{}] Display name set to {!r}", self.name, self.display_name,
                )
        except Exception as e:
            logger.warning("[{}] Could not set display name: {}", self.name, e)

    async def _download_media(self, mxc_url: str) -> bytes | None:
        """Download a file from Matrix via the authenticated media API.

        Uses `/_matrix/client/v1/media/download/...` with the bot's
        access token — the authenticated endpoint, not nio's default
        (which targets the now-deprecated unauthenticated media API).
        Returns the bytes, or None on any non-200 (logged).
        """
        rest = mxc_url.replace("mxc://", "")
        server_name, _, media_id = rest.partition("/")
        download_url = (
            f"{self.homeserver}/_matrix/client/v1/media/download/"
            f"{server_name}/{media_id}"
        )
        session = self._ensure_http()
        async with session.get(
            download_url,
            headers={"Authorization": f"Bearer {self._client.access_token}"},
        ) as resp:
            if resp.status == 200:
                return await resp.read()
            body = await resp.text()
            logger.error(
                "[{}] Media download failed (HTTP {}): {}",
                self.name, resp.status, body,
            )
            return None

    async def _upload_media(
        self, data: bytes, filename: str, content_type: str,
    ) -> str | None:
        """Upload bytes to the media repo, returning the ``mxc://`` URI.

        Goes straight to ``/_matrix/media/v3/upload`` with the bot's token
        (same authenticated-HTTP approach as `_download_media`, sidestepping
        nio's data-provider upload API). Returns None on any non-200 (logged).
        """
        session = self._ensure_http()
        url = f"{self.homeserver}/_matrix/media/v3/upload"
        async with session.post(
            url,
            params={"filename": filename},
            data=data,
            headers={
                "Authorization": f"Bearer {self._client.access_token}",
                "Content-Type": content_type or "application/octet-stream",
            },
        ) as resp:
            if resp.status == 200:
                return (await resp.json()).get("content_uri")
            body = await resp.text()
            logger.error(
                "[{}] Media upload failed (HTTP {}): {}",
                self.name, resp.status, body,
            )
            return None

    async def send_file(
        self,
        room_id: str,
        *,
        data: bytes,
        filename: str,
        mimetype: str,
        msgtype: str = "m.file",
        caption: str | None = None,
        thread_root_event_id: str | None = None,
        metadata: dict | None = None,
    ) -> str | None:
        """Upload bytes and post them as an ``m.file``/``m.image`` message.

        Framework plumbing so any bot can hand a file to the room without
        reimplementing upload + the event shape. ``caption`` rides in ``body``
        (MSC4274: ``filename`` is the real name, ``body`` the human note) so a
        downstream consumer can read it as a hint while keeping the true
        filename. ``thread_root_event_id`` threads it under a root (e.g. an
        email's message), ``metadata`` merges custom keys (a future
        attachment-reference block). Returns the event id, or None on failure.
        """
        mxc = await self._upload_media(data, filename, mimetype)
        if not mxc:
            return None
        content: dict = {
            "msgtype": msgtype,
            "body": caption or filename,
            "filename": filename,
            "url": mxc,
            "info": {"mimetype": mimetype, "size": len(data)},
        }
        if thread_root_event_id:
            content["m.relates_to"] = {
                "rel_type": "m.thread",
                "event_id": thread_root_event_id,
                "is_falling_back": True,
                "m.in_reply_to": {"event_id": thread_root_event_id},
            }
        if metadata:
            content.update(metadata)
        try:
            resp = await self._client.room_send(
                room_id=room_id, message_type="m.room.message", content=content,
            )
        except Exception as e:
            logger.warning("[{}] send_file failed: {}", self.name, e)
            return None
        return getattr(resp, "event_id", None)

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

    async def on_room_joined(self, room_id: str) -> None:
        """Called once after the bot has joined a room AND its state
        is populated by the first sync that includes it.

        Hook for join-time behaviour like posting a welcome message.
        Default is a no-op so subclasses opt in explicitly. Contract:
        ``self._client.rooms[room_id]`` is guaranteed to exist and to
        carry members + name when this fires. The framework wires
        invite-accept to a sync-response callback to enforce that
        contract so subclasses do not need to poll or defer.
        """

        pass

    async def _on_sync_for_pending_joins(self, resp) -> None:
        """SyncResponse callback that drains ``_pending_room_joins``.

        For every room id queued by the invite-accept handler, check
        whether nio has populated its state in this sync. If so, fire
        ``on_room_joined`` exactly once and discard the entry.
        Rooms whose state hasn't arrived yet stay queued for the next
        sync.
        """

        if not isinstance(resp, SyncResponse) or not self._pending_room_joins:
            return
        for room_id in list(self._pending_room_joins):
            room = self._client.rooms.get(room_id) if self._client else None
            if room is None or not getattr(room, "users", None):
                logger.debug(
                    "[{}] joined {} but state not in client.rooms yet, "
                    "waiting for next sync", self.name, room_id,
                )
                continue
            self._pending_room_joins.discard(room_id)
            logger.info(
                "[{}] room state ready, firing on_room_joined({})",
                self.name, room_id,
            )
            try:
                await self.on_room_joined(room_id)
            except Exception as e:
                logger.warning(
                    "[{}] on_room_joined({}) failed: {}",
                    self.name, room_id, e,
                )

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
    def strip_mention(
        body: str, bot_user_id: str, *, formatted_body: str | None = None,
    ) -> str:
        """Remove the bot mention (and any clinging punctuation/whitespace)
        from a message body.

        Used after ``_is_bot_mentioned`` to recover the *actual* query
        the user typed: ``"@archivist-bot:home.local Pollos?"`` → ``"Pollos?"``.
        Idempotent: callers can apply it unconditionally; if neither
        mxid nor display-name mention is present, the body is returned
        unchanged.

        Three rendering paths to handle:
          1. Legacy: the raw mxid appears in ``body``. Strip it.
          2. Modern: clients with ``m.mentions`` support (Element X,
             Element-web) render the mention as the bot's DISPLAY NAME
             in ``body`` (``"Archivist: find MLX"``) and put the mxid
             only inside the HTML ``formatted_body``. We pull the
             display name from the formatted_body's anchor element and
             strip it from the start of ``body``.
          3. Neither: nothing to strip.

        Conservative on punctuation: trims a single trailing ``:`` or
        ``,`` after the mention (common in tab-complete output) and
        collapses the surrounding whitespace, nothing more.
        """
        if not body:
            return body

        # Path 1: raw mxid in body.
        if bot_user_id in body:
            cleaned = body.replace(bot_user_id, " ", 1)
            cleaned = cleaned.replace(" : ", " ").replace(" , ", " ")
            return " ".join(cleaned.split()).strip()

        # Path 2: anchored mention in formatted_body. Find the anchor
        # whose href points at the bot's mxid, take its text content,
        # and strip that prefix from `body`. Display names are
        # user-controlled; this also tolerates Element's
        # ``"DisplayName: "`` colon suffix and the bare-token form.
        if formatted_body:
            import re
            pattern = re.compile(
                r'<a[^>]+href="(?:https?://)?matrix\.to/#/'
                + re.escape(bot_user_id)
                + r'"[^>]*>([^<]+)</a>',
                re.IGNORECASE,
            )
            match = pattern.search(formatted_body)
            if match:
                display = match.group(1).strip()
                if display and body.startswith(display):
                    rest = body[len(display):]
                    rest = rest.lstrip()
                    if rest[:1] in {":", ","}:
                        rest = rest[1:].lstrip()
                    return rest.strip()

        return body

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
