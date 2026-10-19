"""A bot vouches for its own device: Matrix cross-signing without a person.

A Matrix app trusts a device when its owner signed it with their
self-signing key, and newer apps are moving to share room keys only with
such devices (MSC4153). A person's app sets this up by asking them to
verify the new login. A bot has nobody to ask, and neither has any other
account no person logs into, so it does it itself at start: it makes a
master key and a self-signing key once, publishes them, and signs its
device with the self-signing key.

That is the account's half. Whether someone's app shows it as verified
is that person's decision, made in their own app; nothing on the server
can set it.

The keys are Ed25519 seeds in a file next to the bot's session, readable
only by its owner: whoever holds them can vouch for devices in the bot's
name. Losing the file means new keys on the next start, which apps show
as a changed identity.

    signing = CrossSigning(http, homeserver, token, "@archivist-bot:home",
                           "ABCDEFGHIJ", password, Path("archivist-bot.cross-signing.json"))
    await signing.vouch()          # "uploaded keys, signed the device"

One set of keys per account, shared by all its devices. Only the device
that owns them makes and publishes them. Any other device of the account
(`owner=False`) reads the same file and signs itself with it, and leaves
everything as it is when there is no file or the server holds other keys:
making keys there would replace the account's identity and leave the
owner's device unsigned.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

USAGES = ("master", "self_signing", "user_signing")


def canonical_json(obj: dict) -> bytes:
    """The form Matrix signs: sorted keys, no spaces, UTF-8."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _unpadded(data: bytes) -> str:
    return base64.b64encode(data).decode().rstrip("=")


class SigningKey:
    """One Ed25519 key from its 32-byte seed, signing JSON as Matrix does."""

    def __init__(self, seed: bytes):
        # The stub types `seed` as str | int; pycryptodome takes the 32 bytes.
        self._key = ECC.construct(curve="ed25519", seed=seed)  # pyright: ignore[reportArgumentType]
        self.public = _unpadded(self._key.public_key().export_key(format="raw"))
        self.id = f"ed25519:{self.public}"

    def sign(self, obj: dict) -> str:
        payload = {k: v for k, v in obj.items() if k not in ("signatures", "unsigned")}
        return _unpadded(eddsa.new(self._key, "rfc8032").sign(canonical_json(payload)))

    def signed(self, obj: dict, user_id: str) -> dict:
        """`obj` with this key's signature added for `user_id`."""
        signatures = {**obj.get("signatures", {})}
        signatures[user_id] = {**signatures.get(user_id, {}), self.id: self.sign(obj)}
        return {**obj, "signatures": signatures}


class CrossSigning:
    """A bot's cross-signing keys, published once, vouching for its device."""

    def __init__(self, http, homeserver: str, token: str, user_id: str, device_id: str,
                 password: str, keys_file: Path, *, owner: bool = True):
        self._http, self._base = http, homeserver.rstrip("/") + "/_matrix/client/v3"
        self._token, self.user_id, self.device_id = token, user_id, device_id
        self._password, self._file = password, Path(keys_file)
        self._owner = owner

    async def vouch(self) -> str:
        """Publish the keys if the server has others, sign this device if
        it is not yet; say what was done."""
        if not self._owner and not self._file.exists():
            return "no keys of the account here, the device stays unsigned"
        keys = self._keys()
        done = []
        published = await self._query()
        master = published.get("master_keys", {}).get(self.user_id, {})
        if keys["master"].id not in master.get("keys", {}):
            if not self._owner:
                return "the account's published keys are others, the device stays unsigned"
            await self._publish(keys)
            done.append("uploaded keys")
        device = published.get("device_keys", {}).get(self.user_id, {}).get(self.device_id)
        if device is None:
            return ", ".join(done + ["no device keys to sign yet"])
        if keys["self_signing"].id in device.get("signatures", {}).get(self.user_id, {}):
            return ", ".join(done) or "device already signed"
        await self._post("/keys/signatures/upload", {
            self.user_id: {self.device_id: keys["self_signing"].signed(device, self.user_id)}})
        return ", ".join(done + ["signed the device"])

    # ── The keys ──────────────────────────────────────────────────────────

    def _keys(self) -> dict[str, SigningKey]:
        """The bot's three keys, made once and kept in its keys file."""
        try:
            seeds = {usage: base64.b64decode(seed)
                     for usage, seed in json.loads(self._file.read_text()).items()}
        except (OSError, ValueError):
            if not self._owner:
                raise
            seeds = {usage: os.urandom(32) for usage in USAGES}
            self._file.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({u: base64.b64encode(s).decode() for u, s in seeds.items()}, f)
        return {usage: SigningKey(seeds[usage]) for usage in USAGES}

    def _key_object(self, usage: str, key: SigningKey) -> dict:
        return {"user_id": self.user_id, "usage": [usage], "keys": {key.id: key.public}}

    async def _publish(self, keys: dict[str, SigningKey]) -> None:
        """Upload the keys, the self-signing and user-signing ones signed by
        the master. Replacing keys the server already has asks for the
        account's password (user-interactive auth); the bot gives its own."""
        master = keys["master"]
        body = {"master_key": self._key_object("master", master)}
        for usage in ("self_signing", "user_signing"):
            body[f"{usage}_key"] = master.signed(self._key_object(usage, keys[usage]),
                                                  self.user_id)
        status, answer = await self._post("/keys/device_signing/upload", body, check=False)
        if status == 401 and "session" in answer:
            body["auth"] = {"type": "m.login.password", "session": answer["session"],
                            "identifier": {"type": "m.id.user", "user": self.user_id},
                            "password": self._password}
            status, answer = await self._post("/keys/device_signing/upload", body, check=False)
        if status != 200:
            raise RuntimeError(f"uploading the cross-signing keys failed ({status}): {answer}")

    # ── The homeserver ───────────────────────────────────────────────────

    async def _query(self) -> dict:
        return (await self._post("/keys/query", {"device_keys": {self.user_id: []}}))[1]

    async def _post(self, path: str, body: dict, *, check: bool = True) -> tuple[int, dict]:
        async with self._http.post(self._base + path, json=body,
                                   headers={"Authorization": f"Bearer {self._token}"}) as resp:
            answer = await resp.json(content_type=None)
            if check and resp.status != 200:
                raise RuntimeError(f"{path} failed ({resp.status}): {answer}")
            return resp.status, answer
