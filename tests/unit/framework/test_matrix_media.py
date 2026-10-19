"""Files reach a Matrix account the same way from encrypted and unencrypted rooms.

An attachment sent into an encrypted room arrives as a `RoomEncrypted*`
event, and the file on the server is ciphertext. Whoever reads it, a bot
handler or anything else, should get the event an unencrypted room would
have delivered and the original bytes. The media repository here is
pytest-httpserver answering on the authenticated media endpoints.
"""

from __future__ import annotations

import sys
from pathlib import Path

import aiohttp
import pytest
from nio import Event, RoomMessageImage
from nio.crypto.attachments import encrypt_attachment

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from stack.matrix.media import Media  # noqa: E402

PHOTO = b"\xff\xd8 a photo of the picnic"
DOWNLOAD = "/_matrix/client/v1/media/download/server/picnic"


def _encrypted_photo(keys: dict) -> Event:
    return Event.parse_decrypted_event({
        "type": "m.room.message", "event_id": "$photo", "sender": "@homer:server",
        "origin_server_ts": 1, "content": {
            "msgtype": "m.image", "body": "picnic.jpg",
            "file": {"url": "mxc://server/picnic", "v": "v2", **keys}}})


@pytest.fixture
async def http():
    async with aiohttp.ClientSession() as session:
        yield session


@pytest.fixture
def media(httpserver):
    return Media(httpserver.url_for("").rstrip("/"), name="test")


class TestAnAttachmentFromAnEncryptedRoom:

    async def test_becomes_the_plain_event_and_downloads_decrypted(self, media, http, httpserver):
        ciphertext, keys = encrypt_attachment(PHOTO)
        httpserver.expect_request(DOWNLOAD, headers={"Authorization": "Bearer tok"}) \
            .respond_with_data(ciphertext)

        event = media.plain(_encrypted_photo(keys))

        assert isinstance(event, RoomMessageImage)
        assert event.url == "mxc://server/picnic"
        assert await media.download(http, "tok", event.url) == PHOTO

    async def test_an_attachment_from_an_unencrypted_room_downloads_as_it_is(self, media, http,
                                                                            httpserver):
        httpserver.expect_request(DOWNLOAD).respond_with_data(PHOTO)
        event = RoomMessageImage.parse_event({
            "type": "m.room.message", "event_id": "$photo", "sender": "@homer:server",
            "origin_server_ts": 1, "content": {
                "msgtype": "m.image", "body": "picnic.jpg", "url": "mxc://server/picnic"}})

        assert media.plain(event) is event
        assert await media.download(http, "tok", "mxc://server/picnic") == PHOTO


class TestTheMediaRepository:

    async def test_a_missing_file_downloads_as_none(self, media, http, httpserver):
        httpserver.expect_request(DOWNLOAD).respond_with_json({"errcode": "M_NOT_FOUND"},
                                                              status=404)

        assert await media.download(http, "tok", "mxc://server/picnic") is None

    async def test_an_upload_answers_with_the_files_address(self, media, http, httpserver):
        httpserver.expect_request(
            "/_matrix/media/v3/upload", method="POST", query_string="filename=scan.pdf",
            headers={"Authorization": "Bearer tok", "Content-Type": "application/pdf"},
            data=b"%PDF",
        ).respond_with_json({"content_uri": "mxc://server/scan"})

        assert await media.upload(http, "tok", b"%PDF", "scan.pdf",
                                  "application/pdf") == "mxc://server/scan"

    async def test_a_refused_upload_answers_none(self, media, http, httpserver):
        httpserver.expect_request("/_matrix/media/v3/upload", method="POST") \
            .respond_with_json({"errcode": "M_TOO_LARGE"}, status=413)

        assert await media.upload(http, "tok", b"%PDF", "scan.pdf", "application/pdf") is None
