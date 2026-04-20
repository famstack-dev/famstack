"""Matrix integration helpers.

Logging in is done synchronously via urllib so the session fixture
doesn't need an async event loop. Tests that need an AsyncClient create
one per test (function-scoped fixture), reusing the pre-obtained
access token.
"""

from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


SYNAPSE_URL = "http://localhost:42031"


@dataclass
class MatrixCreds:
    """Credentials for a logged-in Matrix user.

    Tests use these to spin up an nio AsyncClient without paying the
    login round-trip on every test:

        client = AsyncClient(creds.homeserver, creds.user_id)
        client.access_token = creds.access_token
        client.device_id = creds.device_id
    """

    homeserver: str
    server_name: str
    user_id: str        # full Matrix ID: @homer:test.local
    username: str       # localpart: homer
    password: str
    access_token: str
    device_id: str


def login(server_name: str, username: str, password: str,
          homeserver: str = SYNAPSE_URL) -> MatrixCreds:
    """Password login against Synapse — returns access token + device id.

    Uses urllib to stay sync and dependency-free at this layer. The
    actual Matrix traffic tests run later (via nio.AsyncClient) uses
    the token returned here.
    """
    payload = json.dumps({
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": username},
        "password": password,
        "device_id": f"test-{username}",
        "initial_device_display_name": f"integration-test-{username}",
    }).encode()

    req = urllib.request.Request(
        f"{homeserver}/_matrix/client/v3/login",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise MatrixLoginError(
            f"Login failed for {username}: {e.code} {e.read().decode(errors='replace')}"
        ) from e

    return MatrixCreds(
        homeserver=homeserver,
        server_name=server_name,
        user_id=f"@{username}:{server_name}",
        username=username,
        password=password,
        access_token=body["access_token"],
        device_id=body["device_id"],
    )


class MatrixLoginError(RuntimeError):
    pass


# ── Room + file helpers on top of nio AsyncClient ────────────────────────
#
# Tests feed files into the archivist the way a real family member would:
# resolve the room alias, upload the bytes, send an m.image or m.file
# event. Then poll the room for the bot's reply.


async def resolve_room(client, alias: str) -> str:
    """Turn '#documents:test.local' into a room_id. Raises on failure."""
    from nio import RoomResolveAliasResponse
    resp = await client.room_resolve_alias(alias)
    if not isinstance(resp, RoomResolveAliasResponse):
        raise RuntimeError(f"Could not resolve {alias}: {resp}")
    return resp.room_id


async def wait_for_room(client, alias: str, timeout: float = 60.0) -> str:
    """Poll room_resolve_alias until the room exists or timeout elapses.

    Bot-created rooms (e.g. #documents) appear asynchronously after the
    bot-runner restarts. `wait_for_room` absorbs that startup race so
    tests don't have to special-case it.
    """
    import asyncio
    from nio import RoomResolveAliasResponse

    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        resp = await client.room_resolve_alias(alias)
        if isinstance(resp, RoomResolveAliasResponse):
            return resp.room_id
        last = resp
        await asyncio.sleep(2)
    raise RuntimeError(f"Room {alias} did not appear within {timeout}s: {last}")


async def ensure_joined(client, room_id: str) -> None:
    """Join the room if we're not already in it. Idempotent."""
    # client.rooms populates via /sync — a cheap initial sync guarantees
    # the membership list is current before we decide whether to join.
    if not client.rooms:
        await client.sync(timeout=3000, full_state=True)
    if room_id not in client.rooms:
        from nio import JoinResponse
        resp = await client.join(room_id)
        if not isinstance(resp, JoinResponse):
            raise RuntimeError(f"Could not join {room_id}: {resp}")


async def upload_and_send_file(
    client,
    room_id: str,
    data: bytes,
    filename: str,
    mime_type: str = "application/pdf",
    msgtype: str = "m.file",
) -> str:
    """Upload bytes to Matrix, then post a file/image message in room.
    Returns the event_id of the posted message."""
    from nio import UploadResponse

    upload, _ = await client.upload(
        data_provider=lambda *_: io.BytesIO(data),
        content_type=mime_type,
        filename=filename,
        filesize=len(data),
    )
    if not isinstance(upload, UploadResponse):
        raise RuntimeError(f"Upload failed: {upload}")

    send = await client.room_send(
        room_id=room_id,
        message_type="m.room.message",
        content={
            "msgtype": msgtype,
            "body": filename,
            "url": upload.content_uri,
            "info": {"mimetype": mime_type, "size": len(data)},
        },
    )
    return send.event_id


async def fetch_room_events(client, room_id: str, *, duration: float = 10.0) -> list:
    """Sync for `duration` seconds and return every event the client
    saw land in `room_id`. Callers filter/assert on the returned list.

    Simple: one job, gather. No predicates, no indexes, no special cases
    for multi-match. Tests use standard Python (`next(... for ... in ...)`
    or `[e for e in ... if ...]`) to pick what they need, which keeps
    assertion logic next to the assertions.
    """
    events: list = []
    deadline = time.monotonic() + duration
    next_batch = client.next_batch
    while time.monotonic() < deadline:
        sync = await client.sync(timeout=1000, since=next_batch)
        next_batch = getattr(sync, "next_batch", next_batch)
        rooms = getattr(sync, "rooms", None)
        joined = getattr(rooms, "join", {}) if rooms else {}
        room_info = joined.get(room_id)
        if room_info is not None:
            events.extend(getattr(room_info.timeline, "events", []))
    return events


def event_type(event) -> str:
    """Raw Matrix event type, even for custom types nio doesn't classify."""
    return getattr(event, "source", {}).get("type", "")
