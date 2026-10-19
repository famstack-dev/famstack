"""A bot vouches for its own device, so other users' apps treat it as signed.

Newer Matrix apps share room keys only with devices their owner signed
(MSC4153). A bot has no person to verify its login, so at start it
publishes cross-signing keys once and signs its device with them. The
checks below verify the signatures as the Matrix spec defines them:
Ed25519 over the canonical JSON of the object, signatures and unsigned
left out.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import aiohttp
import pytest
from Crypto.Signature import eddsa
from werkzeug.wrappers import Response

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "lib"))

from stack.matrix.cross_signing import CrossSigning  # noqa: E402

BOT = "@archivist-bot:simpson"
PASSWORD = "bot-password"


def _unpadded_b64(text: str) -> bytes:
    return base64.b64decode(text + "=" * (-len(text) % 4))


def _verifies(public: str, signature: str, obj: dict) -> bool:
    payload = {k: v for k, v in obj.items() if k not in ("signatures", "unsigned")}
    message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                         sort_keys=True).encode()
    try:
        eddsa.new(eddsa.import_public_key(_unpadded_b64(public)), "rfc8032").verify(
            message, _unpadded_b64(signature))
        return True
    except ValueError:
        return False


class Synapse:
    """The three key endpoints, keeping what was uploaded. Replacing keys
    already published asks for the password, as Synapse does."""

    def __init__(self, httpserver, devices=("DEVICE1",)):
        self.devices = {d: {"user_id": BOT, "device_id": d, "algorithms": ["m.megolm.v1.aes-sha2"],
                            "keys": {f"ed25519:{d}": f"key-of-{d}"}, "signatures": {}}
                        for d in devices}
        self.cross: dict = {}
        self.uploads: list[str] = []
        for path, handler in (("/_matrix/client/v3/keys/query", self._query),
                              ("/_matrix/client/v3/keys/device_signing/upload", self._keys),
                              ("/_matrix/client/v3/keys/signatures/upload", self._signatures)):
            httpserver.expect_request(path, method="POST").respond_with_handler(handler)
        self.url = httpserver.url_for("").rstrip("/")

    def _query(self, request):
        answer = {"device_keys": {BOT: self.devices}}
        if self.cross:
            answer["master_keys"] = {BOT: self.cross["master_key"]}
            answer["self_signing_keys"] = {BOT: self.cross["self_signing_key"]}
        return Response(json.dumps(answer), content_type="application/json")

    def _keys(self, request):
        body = request.get_json()
        if self.cross and body.get("auth", {}).get("password") != PASSWORD:
            return Response(json.dumps({"session": "uia", "flows": [{"stages": ["m.login.password"]}]}),
                            status=401, content_type="application/json")
        self.uploads.append("keys")
        self.cross = {k: v for k, v in body.items() if k != "auth"}
        return Response("{}", content_type="application/json")

    def _signatures(self, request):
        self.uploads.append("signatures")
        for device_id, signed in request.get_json()[BOT].items():
            self.devices[device_id]["signatures"] = signed["signatures"]
        return Response('{"failures": {}}', content_type="application/json")

    def public(self, kind: str) -> str:
        return next(iter(self.cross[kind]["keys"].values()))


async def _vouch(synapse, keys_file, device="DEVICE1"):
    async with aiohttp.ClientSession() as http:
        return await CrossSigning(http, synapse.url, "token", BOT, device, PASSWORD,
                                  keys_file).vouch()


@pytest.fixture
def synapse(httpserver):
    return Synapse(httpserver, devices=("DEVICE1", "DEVICE2"))


class TestABotVouchesForItsDevice:

    async def test_a_new_bot_publishes_its_keys_and_signs_its_device(self, synapse, tmp_path):
        outcome = await _vouch(synapse, tmp_path / "keys.json")

        assert outcome == "uploaded keys, signed the device"
        master, self_signing = synapse.public("master_key"), synapse.public("self_signing_key")
        ss_key = synapse.cross["self_signing_key"]
        assert _verifies(master, ss_key["signatures"][BOT][f"ed25519:{master}"], ss_key)
        device = synapse.devices["DEVICE1"]
        assert _verifies(self_signing, device["signatures"][BOT][f"ed25519:{self_signing}"], device)

    async def test_the_next_start_changes_nothing(self, synapse, tmp_path):
        await _vouch(synapse, tmp_path / "keys.json")
        synapse.uploads.clear()

        assert await _vouch(synapse, tmp_path / "keys.json") == "device already signed"
        assert synapse.uploads == []

    async def test_a_new_login_is_signed_with_the_same_keys(self, synapse, tmp_path):
        await _vouch(synapse, tmp_path / "keys.json")
        synapse.uploads.clear()

        assert await _vouch(synapse, tmp_path / "keys.json", device="DEVICE2") == "signed the device"
        assert synapse.uploads == ["signatures"]

    async def test_lost_keys_are_replaced_with_the_bots_own_password(self, synapse, tmp_path):
        await _vouch(synapse, tmp_path / "old.json")
        old = synapse.public("master_key")

        outcome = await _vouch(synapse, tmp_path / "lost-and-new.json")

        assert outcome == "uploaded keys, signed the device"
        assert synapse.public("master_key") != old

    async def test_the_keys_file_is_readable_by_its_owner_only(self, synapse, tmp_path):
        await _vouch(synapse, tmp_path / "keys.json")

        assert (tmp_path / "keys.json").stat().st_mode & 0o077 == 0
