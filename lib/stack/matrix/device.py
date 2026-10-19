"""An account logged in on a device of its own, with the keys that device needs.

A Matrix login is a device: an access token, a device id, and the key
store that lets it read and write encrypted rooms. An account that logs
in afresh on every start gets a new device each time, and the family's
apps cannot encrypt for a device they have not seen yet. So the login is
kept in a file and restored on the next start, the key store with it,
and a password login happens only when the kept one is no longer valid.

Everything lives in one state directory, under the name the caller gives:

    <state_dir>/<name>.session.json        access token, user id, device id
    <state_dir>/<name>_crypto/             the device's key store (nio's)
    <state_dir>/<name>.cross-signing.json  the keys vouching for the device

Every caller uses it the same way, a bot's sync loop or a one-shot sender:

    device = Device("http://synapse:8008", "@notify:home", password,
                    Path("/data/notify"), name="notify")
    if await device.connect(http):
        await device.sync(timeout=10000, full_state=True)
        await device.client.room_send(room_id, "m.room.message",
                                      {"msgtype": "m.text", "body": "Backup done"})
    await device.client.close()
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiohttp
from loguru import logger
from nio import AsyncClient, AsyncClientConfig, LoginResponse

from stack.matrix.cross_signing import CrossSigning


class FamilyClient(AsyncClient):
    """nio's client, sending into an encrypted room without verifying each device first.

    nio refuses to encrypt for a device nobody verified. `Device.sync`
    marks every device it knows as trusted after each sync, but one that
    appeared since would fail the send. On the family's own server every
    device is a family member's, so a send never waits on verification.
    """

    async def room_send(self, *args, ignore_unverified_devices: bool = True, **kwargs):
        return await super().room_send(*args, ignore_unverified_devices=ignore_unverified_devices,
                                       **kwargs)


class Device:
    """One account's login on a device of its own, kept across restarts.

    `connect` logs in (restoring the kept login when the server still
    accepts it) and vouches for the device; `sync` is nio's sync plus the
    key upkeep a bare sync leaves out. Everything else is `client`, the
    nio client, which encrypts for the rooms that need it.
    """

    def __init__(self, homeserver: str, user_id: str, password: str, state_dir: Path, name: str):
        self.homeserver = homeserver
        self.user_id = user_id
        self.name = name
        self._password = password
        self._session_file = state_dir / f"{name}.session.json"
        self._keys_file = state_dir / f"{name}.cross-signing.json"
        store_path = state_dir / f"{name}_crypto"
        store_path.mkdir(parents=True, exist_ok=True)
        config = AsyncClientConfig(store_sync_tokens=True, encryption_enabled=True)
        self.client = FamilyClient(homeserver, user_id, store_path=str(store_path), config=config)

    async def connect(self, http: aiohttp.ClientSession) -> bool:
        """Log in, bring the device's keys up to date and vouch for it.

        False when the account cannot log in at all; the reason is logged.
        `http` is the session the cross-signing requests go through.
        """
        if not await self._log_in():
            logger.error("[{}] Cannot authenticate — giving up", self.name)
            return False
        await self._keep_keys()
        await self._vouch(http)
        return True

    async def sync(self, timeout: int, full_state: bool | None = None):
        """One sync, then the key upkeep, then trust for the devices it brought.

        Returns nio's sync response.
        """
        resp = await self.client.sync(timeout=timeout, full_state=full_state)
        await self._keep_keys()
        self._trust_all_devices()
        return resp

    # ── Logging in ───────────────────────────────────────────────────────

    async def _log_in(self) -> bool:
        """The kept login when the server still accepts it, a new one otherwise."""
        if self._restore_session() and await self._session_valid():
            return True
        return await self._password_login()

    async def _session_valid(self) -> bool:
        """Whether the server still knows the restored login; forgets it if not."""
        try:
            resp = await self.client.whoami()
        except Exception as e:
            logger.warning("[{}] Session check failed: {}, clearing", self.name, e)
            self._clear_session()
            return False
        if getattr(resp, "user_id", None) == self.user_id:
            logger.info("[{}] Session valid for {}", self.name, self.user_id)
            return True
        logger.warning("[{}] Session invalid (whoami returned {}), clearing",
                       self.name, type(resp).__name__)
        self._clear_session()
        return False

    async def _password_login(self, retries=30, interval=10) -> bool:
        """Log in with password, retrying until the account exists.

        On a fresh install, the bot runner starts before messages creates
        the bot accounts. Instead of crashing, we wait.
        """
        if not self._password:
            logger.error("[{}] No password available", self.name)
            return False

        for attempt in range(1, retries + 1):
            resp = await self.client.login(self._password)
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

    # ── The kept login ───────────────────────────────────────────────────

    def _restore_session(self) -> bool:
        """Restore a saved Matrix session."""
        if not self._session_file.exists():
            return False
        try:
            data = json.loads(self._session_file.read_text())
            self.client.access_token = data["access_token"]
            self.client.user_id = data["user_id"]
            self.client.device_id = data["device_id"]
            logger.info("[{}] Restored session (device {})", self.name, data["device_id"])
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("[{}] Bad session file: {}", self.name, e)
            return False
        # A password login opens the device's key store itself; a restored
        # session has to. Without it nio has no olm, and the device neither
        # decrypts nor encrypts from its first restart on.
        try:
            self.client.load_store()
        except Exception as e:
            logger.warning("[{}] Key store not loaded, encrypted rooms stay unread: {}", self.name, e)
        return True

    def _clear_session(self):
        """Wipe saved session and in-memory credentials."""
        self.client.access_token = ""
        self.client.device_id = ""
        if self._session_file.exists():
            self._session_file.unlink()
            logger.info("[{}] Deleted stale session file", self.name)

    def _save_session(self) -> None:
        """Persist the current session so it survives container restarts."""
        self._session_file.parent.mkdir(parents=True, exist_ok=True)
        self._session_file.write_text(json.dumps({
            "access_token": self.client.access_token,
            "user_id": self.client.user_id,
            "device_id": self.client.device_id,
        }))

    # ── Keys ─────────────────────────────────────────────────────────────

    async def _keep_keys(self) -> None:
        """The key upkeep nio's `sync_forever` does and a bare `sync()` does not.

        One-time keys are topped up (the family's clients use one each to
        open a session and send the device a room's key), other people's
        devices are queried and their sessions claimed, and queued
        to-device messages go out. Failures are logged; the next sync tries
        again.
        """
        client = self.client
        if not client.olm:
            return
        try:
            if client.should_upload_keys:
                await client.keys_upload()
            if client.should_query_keys:
                await client.keys_query()
            if client.should_claim_keys:
                await client.keys_claim(client.get_users_for_key_claiming())
            await client.send_to_device_messages()
        except Exception as e:
            logger.warning("[{}] Key upkeep failed: {}", self.name, e)

    async def _vouch(self, http: aiohttp.ClientSession) -> None:
        """Sign this login with the account's own cross-signing keys (`cross_signing`),
        so the family's apps share room keys with it as with a verified device."""
        if not self.client.olm:
            return
        try:
            outcome = await CrossSigning(
                http, self.homeserver, self.client.access_token, self.user_id,
                self.client.device_id, self._password, self._keys_file).vouch()
            logger.info("[{}] Cross-signing: {}", self.name, outcome)
        except Exception as e:  # an unsigned device still works with most apps today
            logger.warning("[{}] Cross-signing failed, the device stays unsigned: {}", self.name, e)

    def _trust_all_devices(self) -> None:
        """Mark all known devices as trusted without interactive verification."""
        if not self.client.olm:
            return
        try:
            for user_id in self.client.device_store.users:
                for device in self.client.device_store.active_user_devices(user_id):
                    if not self.client.olm.is_device_verified(device):
                        self.client.verify_device(device)
                        logger.info("[{}] Trusted device {} of {}", self.name, device.device_id, user_id)
        except Exception as e:
            logger.debug("[{}] Trust devices: {}", self.name, e)
