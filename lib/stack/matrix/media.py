"""Files in Matrix: the media repository, and attachments from encrypted rooms.

A photo, PDF or voice message sent into an encrypted room arrives as an
event nio parses as `RoomEncrypted*`, with the file's address and the key
that opens it in `content.file`; the file on the server is ciphertext.
`Media.plain` turns such an event into the plain one an unencrypted room
would have delivered and keeps the key, so `Media.download` hands back the
same bytes from either kind of room.

    media = Media("http://synapse:8008", name="archivist-bot")
    event = media.plain(event)                          # a RoomMessageImage now
    photo = await media.download(http, token, event.url)  # decrypted
    uri = await media.upload(http, token, pdf, "scan.pdf", "application/pdf")

Both directions use the authenticated media API with the account's access
token; `http` is the caller's aiohttp session.
"""

from __future__ import annotations

import copy

import aiohttp
from loguru import logger
from nio import RoomEncryptedMedia, RoomMessage
from nio.crypto.attachments import decrypt_attachment

# Encrypted attachments whose key is kept for a later download; the oldest go first.
MEDIA_KEYS_KEPT = 500


class Media:
    """One account's way to the media repository, holding the keys of the encrypted attachments it has seen."""

    def __init__(self, homeserver: str, name: str):
        self.homeserver = homeserver
        self.name = name
        # The keys of encrypted attachments seen, by their mxc:// address.
        self._keys: dict[str, tuple[str, str, str]] = {}

    def plain(self, event):
        """An encrypted photo, file or voice message as the plain event handlers expect.

        nio parses a decrypted attachment as `RoomEncrypted*`, which no
        handler listens for, and keeps its address in `content.file`
        together with the key to open it. The plain event carries the
        address in `content.url`, as an unencrypted one does; the key is
        kept for `download`. Any other event comes back as it is.
        """
        if not isinstance(event, RoomEncryptedMedia):
            return event
        source = copy.deepcopy(event.source)
        content = source.get("content", {})
        file = content.pop("file", None) or {}
        try:
            url, key = file["url"], (file["key"]["k"], file["hashes"]["sha256"], file["iv"])
        except (KeyError, TypeError):
            return event
        content["url"] = url
        self._keys[url] = key
        while len(self._keys) > MEDIA_KEYS_KEPT:
            self._keys.pop(next(iter(self._keys)))
        return RoomMessage.parse_event(source)

    async def download(self, http: aiohttp.ClientSession, token: str,
                       mxc_url: str) -> bytes | None:
        """Download a file from Matrix via the authenticated media API.

        Uses `/_matrix/client/v1/media/download/...` with the access
        token: the authenticated endpoint, not nio's default (which
        targets the now-deprecated unauthenticated media API). Returns
        the bytes, or None on any non-200 (logged). An attachment from an
        encrypted room is decrypted with the key its event carried.
        """
        rest = mxc_url.replace("mxc://", "")
        server_name, _, media_id = rest.partition("/")
        download_url = (
            f"{self.homeserver}/_matrix/client/v1/media/download/"
            f"{server_name}/{media_id}"
        )
        async with http.get(
            download_url,
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            if resp.status == 200:
                data = await resp.read()
                key = self._keys.get(mxc_url)
                return decrypt_attachment(data, *key) if key else data
            body = await resp.text()
            logger.error(
                "[{}] Media download failed (HTTP {}): {}",
                self.name, resp.status, body,
            )
            return None

    async def upload(self, http: aiohttp.ClientSession, token: str, data: bytes,
                     filename: str, content_type: str) -> str | None:
        """Upload bytes to the media repo, returning the ``mxc://`` URI.

        Goes straight to ``/_matrix/media/v3/upload`` with the access
        token (the same authenticated-HTTP approach as `download`,
        sidestepping nio's data-provider upload API). Returns None on any
        non-200 (logged).
        """
        url = f"{self.homeserver}/_matrix/media/v3/upload"
        async with http.post(
            url,
            params={"filename": filename},
            data=data,
            headers={
                "Authorization": f"Bearer {token}",
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
