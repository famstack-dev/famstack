"""MicroBot transport against real Synapse.

The framework's Matrix I/O — `_send` (formatted reply + reply relation
+ custom metadata), `_reply_parent_envelope` (read the envelope off a
replied-to message), `_download_media` (authenticated media fetch) —
verified against a live Synapse rather than a stub.

The FakeClient tests in tests/stacklets/test_microbot.py pin the
*content-dict shape* fast and offline; these pin that Synapse actually
round-trips it — the kind of assumption a stub can quietly get wrong:

  - does a custom top-level content key (`dev.famstack.event`) survive
    storage and come back on `room_get_event` / sync?
  - does `formatted_body` arrive intact, or does Synapse rewrite it?
  - does the authenticated media endpoint
    (`/_matrix/client/v1/media/download/...`) work on this Synapse
    build, or only the deprecated unauthenticated one?

Run via the rig (brings up `messages`/Synapse, Simpsons family logged in):

    tests/integration/stacktests pytest \
        tests/integration/test_microbot_transport_e2e.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))

from microbot import MicroBot  # noqa: E402
from tests.integration.matrix import wait_for_room_event  # noqa: E402


def _bot_over(client) -> MicroBot:
    """Wrap a logged-in nio client in a bare MicroBot.

    The bot's identity IS the client's user — so `_reply_parent_envelope`'s
    owner check (`sender == self.user_id`) passes for messages this
    client sent, which is what these happy-path tests exercise.
    """
    class _Bot(MicroBot):
        name = "transport-test-bot"

        def register_callbacks(self, c):
            return None

    bot = _Bot(
        homeserver=client.homeserver,
        user_id=client.user_id,
        password="x",
        session_dir="/tmp",  # never written: no start(), no session save
    )
    bot._client = client
    return bot


async def _new_room(client) -> str:
    from nio.api import RoomVisibility
    resp = await client.room_create(
        name="microbot-transport-test",
        visibility=RoomVisibility.private,
    )
    room_id = getattr(resp, "room_id", None)
    assert room_id, f"room_create failed: {resp}"
    return room_id


async def _find_sent(client, room_id: str, body: str):
    """Sync the room and return the event whose plaintext body matches."""
    return await wait_for_room_event(
        client,
        room_id,
        lambda e: e.source.get("content", {}).get("body") == body,
        timeout=8.0,
    )


# ── _send: the formatted-reply path ──────────────────────────────────────


@pytest.mark.smoke
async def test_send_round_trips_markdown_reply_and_envelope(homer):
    """`_send` → Synapse → the stored event keeps the rendered HTML, the
    reply relation, and the custom `dev.famstack.event` envelope.

    This is the live counterpart to test_microbot.TestSend: the stub
    proves we *build* the right content dict; this proves Synapse
    *preserves* it end to end — the contract the reply-to-reprocess
    flow depends on.
    """
    bot = _bot_over(homer)
    room_id = await _new_room(homer)

    anchor = await homer.room_send(
        room_id, "m.room.message", {"msgtype": "m.text", "body": "anchor"},
    )

    envelope = {
        "dev.famstack.event": {
            "type": "document.filed",
            "data": {"paperless_id": 7},
        },
    }
    await bot._send(
        room_id, "**Filed** the invoice",
        reply_to=anchor.event_id, metadata=envelope,
    )

    sent = await _find_sent(homer, room_id, "**Filed** the invoice")
    assert sent is not None, "sent message never landed in the room"
    content = sent.source["content"]
    assert "<strong>Filed</strong>" in content["formatted_body"]
    assert content["m.relates_to"]["m.in_reply_to"]["event_id"] == anchor.event_id
    assert content["dev.famstack.event"]["data"]["paperless_id"] == 7


# ── _reply_parent_envelope: read an envelope off a replied-to message ─────


async def test_reply_parent_envelope_reads_our_filing(homer):
    """A reply to one of our `dev.famstack.event` messages resolves back
    to the envelope via `room_get_event`. Underpins the archivist's
    reply-to-reprocess (`reply → paperless_id`)."""
    bot = _bot_over(homer)
    room_id = await _new_room(homer)

    envelope = {
        "dev.famstack.event": {
            "type": "document.filed",
            "data": {"paperless_id": 11},
        },
    }
    await bot._send(room_id, "Filed #11", metadata=envelope)
    filing = await _find_sent(homer, room_id, "Filed #11")
    assert filing is not None, "filing message never landed"

    # A reply pointing at the filing — only the relation matters here;
    # the bot fetches the parent itself.
    reply_event = SimpleNamespace(
        source={"content": {"m.relates_to": {
            "m.in_reply_to": {"event_id": filing.event_id},
        }}},
    )
    got = await bot._reply_parent_envelope(room_id, reply_event)
    assert got == {"type": "document.filed", "data": {"paperless_id": 11}}


async def test_reply_parent_envelope_none_without_relation(homer):
    """A message that isn't a reply yields no envelope."""
    bot = _bot_over(homer)
    room_id = await _new_room(homer)
    plain = SimpleNamespace(source={"content": {"body": "just chatting"}})
    assert await bot._reply_parent_envelope(room_id, plain) is None


# ── _download_media: authenticated media fetch ───────────────────────────


async def test_download_media_round_trips_bytes(homer):
    """Upload bytes, then pull them back through `_download_media`.

    Verifies the authenticated `/_matrix/client/v1/media/download/...`
    endpoint works on this Synapse build — the reason the archivist
    hand-rolled the fetch instead of using nio's deprecated default.
    """
    from nio import UploadResponse

    bot = _bot_over(homer)
    data = b"%PDF-1.4 transport-test payload " + b"x" * 64

    upload, _ = await homer.upload(
        data_provider=lambda *_: io.BytesIO(data),
        content_type="application/pdf",
        filename="transport-test.pdf",
        filesize=len(data),
    )
    assert isinstance(upload, UploadResponse), f"upload failed: {upload}"

    got = await bot._download_media(upload.content_uri)
    assert got == data
