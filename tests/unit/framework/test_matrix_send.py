"""The one-shot sender's device: one per account, and one send at a time.

Two processes on one key store corrupt it, so two sends as the same
account take turns: the second waits until the first has finished. The
device shows up in a person's device list under a name that says what it
is. The send itself (login, keys, encryption) is checked against a real
Synapse on a running instance.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import aiohttp
import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "lib"))

from stack.matrix.send import attach, attachment, device_names, exclusive  # noqa: E402

# A second process taking the same account's lock, as a second send would.
SECOND_SEND = (
    "import sys; from pathlib import Path; from stack.matrix.send import exclusive\n"
    "with exclusive(Path(sys.argv[1])): print('sent')\n"
)


def _second_send(lock: Path, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", SECOND_SEND, str(lock)],
                          capture_output=True, text=True, timeout=timeout,
                          env={"PYTHONPATH": str(_REPO / "lib")})


def test_a_second_send_as_the_same_account_waits_for_the_first(tmp_path):
    lock = tmp_path / "stacky-bot-cli.lock"

    with exclusive(lock):
        with pytest.raises(subprocess.TimeoutExpired):
            _second_send(lock, timeout=1.5)

    assert _second_send(lock, timeout=10).stdout.strip() == "sent"


def test_the_device_is_named_after_its_account():
    assert device_names("@stacky-bot:simpson") == ("stacky-bot-cli", "stacky-bot (command line)")


def test_a_file_arrives_in_the_request_as_its_bytes():
    """The host sends the file in base64, since the request is JSON on stdin."""
    assert attachment({"attachment": "JVBERi0xLjc="}) == b"%PDF-1.7"
    assert attachment({"content": {"msgtype": "m.text"}}) is None


async def test_a_file_the_server_refuses_fails_the_send_with_the_servers_reason(httpserver):
    """Given a file over the homeserver's size limit, when the device attaches
    it, then the send fails with the server's own reason, which the person
    then reads after `stack messages upload`."""
    httpserver.expect_request("/_matrix/media/v3/upload", method="POST").respond_with_json(
        {"errcode": "M_TOO_LARGE", "error": "Cannot upload files larger than 100M"}, status=413)

    async with aiohttp.ClientSession() as http:
        with pytest.raises(RuntimeError, match="Cannot upload files larger than 100M"):
            await attach(http, httpserver.url_for("").rstrip("/"), "homer-cli", "tok",
                         {"msgtype": "m.file", "body": "film.mov"}, b"a long film")


async def test_an_attached_file_is_in_the_content_encrypted(httpserver):
    httpserver.expect_request("/_matrix/media/v3/upload", method="POST") \
        .respond_with_json({"content_uri": "mxc://simpson/film"})

    async with aiohttp.ClientSession() as http:
        content = await attach(http, httpserver.url_for("").rstrip("/"), "homer-cli", "tok",
                               {"msgtype": "m.file", "body": "film.mov"}, b"a long film")

    assert content["body"] == "film.mov" and "url" not in content
    assert content["file"]["url"] == "mxc://simpson/film"
    assert set(content["file"]) == {"url", "key", "iv", "hashes", "v"}
