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

from loguru import logger
from nio import (
    AsyncClient,
    AsyncClientConfig,
    InviteMemberEvent,
    LoginResponse,
    MegolmEvent,
)


class MicroBot:
    """Base class for lightweight Matrix bots.

    Subclasses must set `name` and implement `register_callbacks()`.
    All bot-specific config from bot.toml [settings] arrives as kwargs
    and is stored in `self.config` for the subclass to read.
    """

    name: str = "bot"

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
        """Register an event callback with cursor-based dedup.

        Wraps the callback so it only fires for messages newer than the
        last processed timestamp (per room). The cursor is advanced
        before the callback runs — at-most-once delivery.
        """
        async def wrapper(room, event):
            if event.sender == self.user_id:
                return
            ts = getattr(event, "server_timestamp", 0)
            if ts <= self._cursors.get(room.room_id, 0):
                return
            self._advance_cursor(room.room_id, ts)
            await callback(room, event)

        self._client.add_event_callback(wrapper, event_type)

    async def on_first_sync(self) -> None:
        """Called once after the very first sync. Override to send welcome
        messages, announce the bot to rooms, etc. Not called on restarts."""
        pass

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
