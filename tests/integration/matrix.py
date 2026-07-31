"""Matrix integration helpers.

Logging in is done synchronously via urllib so the session fixture
doesn't need an async event loop. Tests that need an AsyncClient create
one per test (function-scoped fixture), reusing the pre-obtained
access token.
"""

from __future__ import annotations

import io
import json
import sys
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


SYNAPSE_URL = "http://localhost:42031"

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def realm() -> str:
    """The instance's Matrix server name, read from the live stack.toml.

    Both lanes run against one Simpsons instance, so no test may hardcode
    this. Synapse bakes server_name into every user ID permanently at first
    start; a literal that drifts from the running homeserver turns every
    login into a 403 that names neither the realm nor the cause. That is
    precisely how the dev instance lost all four of its bots.
    """
    stack_toml = _REPO_ROOT / "stack.toml"
    if not stack_toml.exists():
        raise RuntimeError(
            f"{stack_toml} is missing — bring the instance up first "
            "(tests/integration/stacktests up), which seeds it."
        )
    with stack_toml.open("rb") as fh:
        name = tomllib.load(fh).get("messages", {}).get("server_name")
    if not name:
        raise RuntimeError(f"No [messages].server_name in {stack_toml}.")
    return name


def mxid(localpart: str) -> str:
    """`@homer` -> `@homer:<realm>`."""
    return f"@{localpart}:{realm()}"


def room_alias(name: str) -> str:
    """`documents` -> `#documents:<realm>`."""
    return f"#{name}:{realm()}"


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


def token_alive(access_token: str, homeserver: str = SYNAPSE_URL) -> bool:
    """Whether Synapse still honours this access token.

    `/whoami` is the cheapest way to ask. Synapse invalidates an account's
    devices on a password change, so this is how a test observes a session
    being ended out from under it.
    """
    req = urllib.request.Request(
        f"{homeserver}/_matrix/client/v3/account/whoami",
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except urllib.error.HTTPError:
        return False


def deactivate_user(admin_token: str, mxid: str,
                    homeserver: str = SYNAPSE_URL) -> None:
    """Deactivate and erase an account. Best effort, for test cleanup.

    Synapse never truly deletes users, so a test that creates one has to
    at least leave it deactivated rather than leaking a live account into
    the demo instance.
    """
    req = urllib.request.Request(
        f"{homeserver}/_synapse/admin/v1/deactivate/{mxid}",
        data=json.dumps({"erase": True}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {admin_token}",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as e:
        print(f"[cleanup] could not deactivate {mxid}: {e.code}", file=sys.stderr)


# ── Room + file helpers on top of nio AsyncClient ────────────────────────
#
# Tests feed files into the archivist the way a real family member would:
# resolve the room alias, upload the bytes, send an m.image or m.file
# event. Then poll the room for the bot's reply.

async def matrix_call(label: str, awaitable, *, timeout: float = 15.0):
    """Run one nio request with a hard client-side timeout.

    aiohttp/nio calls can otherwise hang below a helper's own polling
    deadline, making live integration tests look like they are still
    waiting normally. Fail at the Matrix operation instead.
    """
    import asyncio

    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except asyncio.TimeoutError as e:
        raise RuntimeError(f"Matrix call timed out after {timeout:g}s: {label}") from e


async def resolve_room(client, alias: str) -> str:
    """Turn '#documents:test.local' into a room_id. Raises on failure."""
    from nio import RoomResolveAliasResponse
    resp = await matrix_call(f"resolve room {alias}", client.room_resolve_alias(alias))
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
        resp = await matrix_call(
            f"resolve room {alias}", client.room_resolve_alias(alias),
        )
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
        await matrix_call(
            "initial sync before join",
            client.sync(timeout=3000, full_state=True),
        )
    if room_id not in client.rooms:
        from nio import JoinResponse
        resp = await matrix_call(f"join {room_id}", client.join(room_id))
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

    upload, _ = await matrix_call(
        f"upload {filename}",
        client.upload(
            data_provider=lambda *_: io.BytesIO(data),
            content_type=mime_type,
            filename=filename,
            filesize=len(data),
        ),
        timeout=60,
    )
    if not isinstance(upload, UploadResponse):
        raise RuntimeError(f"Upload failed: {upload}")

    send = await matrix_call(
        f"send file message to {room_id}",
        client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={
                "msgtype": msgtype,
                "body": filename,
                "url": upload.content_uri,
                "info": {"mimetype": mime_type, "size": len(data)},
            },
        ),
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
        sync = await matrix_call(
            f"sync events for {room_id}",
            client.sync(timeout=1000, since=next_batch),
            timeout=3,
        )
        next_batch = getattr(sync, "next_batch", next_batch)
        rooms = getattr(sync, "rooms", None)
        joined = getattr(rooms, "join", {}) if rooms else {}
        room_info = joined.get(room_id)
        if room_info is not None:
            events.extend(getattr(room_info.timeline, "events", []))
    return events


async def wait_for_room_event(
    client,
    room_id: str,
    predicate,
    *,
    timeout: float = 45.0,
):
    """Return the first room event matching `predicate`.

    Matrix `/sync` is already a long-poll API: the server holds the
    request until something changes or the request timeout expires. This
    gives tests webhook-like behavior without adding a Synapse plugin or
    sidecar. Positive waits return as soon as the event arrives instead
    of collecting for a fixed wall-clock window.
    """
    deadline = time.monotonic() + timeout
    next_batch = client.next_batch
    while time.monotonic() < deadline:
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        sync_timeout = min(30_000, remaining_ms)
        sync = await matrix_call(
            f"sync waiting for event in {room_id}",
            client.sync(timeout=sync_timeout, since=next_batch),
            timeout=(sync_timeout / 1000) + 5,
        )
        next_batch = getattr(sync, "next_batch", next_batch)
        rooms = getattr(sync, "rooms", None)
        joined = getattr(rooms, "join", {}) if rooms else {}
        room_info = joined.get(room_id)
        if room_info is None:
            continue
        for event in getattr(room_info.timeline, "events", []):
            if predicate(event):
                return event
    return None


async def wait_for_room_events_until(
    client,
    room_id: str,
    predicate,
    *,
    timeout: float = 45.0,
) -> list:
    """Collect room events until `predicate(events)` is true.

    Use this when the assertion depends on a sequence, for example a
    progress reaction followed by a completion reaction. Returning the
    whole collected batch avoids losing events that arrive together in
    the same `/sync` response.
    """
    events: list = []
    deadline = time.monotonic() + timeout
    next_batch = client.next_batch
    while time.monotonic() < deadline:
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        sync_timeout = min(30_000, remaining_ms)
        sync = await matrix_call(
            f"sync waiting for event batch in {room_id}",
            client.sync(timeout=sync_timeout, since=next_batch),
            timeout=(sync_timeout / 1000) + 5,
        )
        next_batch = getattr(sync, "next_batch", next_batch)
        rooms = getattr(sync, "rooms", None)
        joined = getattr(rooms, "join", {}) if rooms else {}
        room_info = joined.get(room_id)
        if room_info is None:
            continue
        events.extend(getattr(room_info.timeline, "events", []))
        if predicate(events):
            return events
    return events


def event_type(event) -> str:
    """Raw Matrix event type, even for custom types nio doesn't classify."""
    return getattr(event, "source", {}).get("type", "")
