"""The memories room as the diary command reads it, and the files it keeps.

    token = await admin_token(session, homeserver)
    room_id = await resolve_room(session, homeserver, token, "memories")
    events = await history(session, homeserver, token, room_id, limit=0)
    messages, kept = await keep_files(messages, events, session=session, ...)
    remember_room(room_id)          # for `diary pages` and `diary correct`

The command reads the room through the homeserver's admin API, page by
page, and downloads each file once into the media store. A photo's
capture day comes from the copy kept there (`stack.media_dates`), so the
messages leave this module with the day their photos were taken.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.parse import quote

# httpx rather than aiohttp: the OpenAI SDK already pulls it into every
# container that can talk to a model, so the compiler runs unchanged in
# the bot-runner and in the curator that schedules it nightly.
import httpx
from stack import media, media_dates

from ... import diary_store
from ...diary.dating import with_capture_days
from .progress import err

# One page of room history per request. Synapse caps this well above
# 100; the number only trades round trips against response size.
_PAGE = 100


# ── Reading the room ──────────────────────────────────────────────────
#
# Through the Synapse admin API, which reads any room without the
# reader having to be a member. A compiler that had to be invited to the
# memories room would be one more thing to set up, and one more account
# with standing access to the most private room in the house.


async def admin_token(session: httpx.AsyncClient, homeserver: str) -> str:
    user = os.environ.get("MATRIX_ADMIN_USER", "")
    password = os.environ.get("MATRIX_ADMIN_PASSWORD", "")
    if not user or not password:
        raise RuntimeError("MATRIX_ADMIN_USER/PASSWORD not set in this container")
    resp = await session.post(f"{homeserver}/_matrix/client/v3/login", json={
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": user},
        "password": password,
    })
    if resp.status_code != 200:
        raise RuntimeError(f"admin login failed: HTTP {resp.status_code}")
    return resp.json()["access_token"]


async def resolve_room(session, homeserver, token, room: str) -> str:
    if room.startswith("!"):
        return room
    alias = room if room.startswith("#") else \
        f"#{room}:{os.environ.get('MATRIX_SERVER_NAME', '')}"
    resp = await session.get(
        f"{homeserver}/_matrix/client/v3/directory/room/{quote(alias)}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"no such room: {alias}")
    return resp.json()["room_id"]


async def history(session, homeserver, token, room_id: str,
                   limit: int = 0) -> list[dict]:
    """Message events in the room, newest page first.

    Paginates backwards until Synapse stops handing back a cursor. A
    positive ``limit`` stops after that many events: the newest part
    of the room, for a bounded preview run. The caller sorts; order
    here is only what the API gives us.

    Counts out loud as it goes. A room with years in it takes many
    round trips before anything else can start, and a command that
    prints nothing for that long is indistinguishable from one that has
    hung.
    """
    events: list[dict] = []
    cursor = ""
    headers = {"Authorization": f"Bearer {token}"}
    while True:
        url = (f"{homeserver}/_synapse/admin/v1/rooms/{quote(room_id)}"
               f"/messages?dir=b&limit={_PAGE}")
        if cursor:
            url += f"&from={quote(cursor)}"
        resp = await session.get(url, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"could not read room: HTTP {resp.status_code}")
        payload = resp.json()
        chunk = payload.get("chunk") or []
        events.extend(chunk)
        if chunk:
            err(f"  read {len(events)} events so far")
        cursor = payload.get("end") or ""
        if limit and len(events) >= limit:
            return events[:limit]
        if not chunk or not cursor:
            return events


async def download(session, homeserver, token, mxc: str) -> bytes | None:
    server, _, media_id = mxc.replace("mxc://", "").partition("/")
    url = (f"{homeserver}/_matrix/client/v1/media/download/"
           f"{quote(server)}/{quote(media_id)}")
    resp = await session.get(url, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code != 200:
        err(f"  media {mxc}: HTTP {resp.status_code}")
        return None
    return resp.content


# ── Keeping the originals ─────────────────────────────────────────────
#
# The room is the only copy of everything a family posts into it. The
# pages compiled below describe those recordings and photographs and
# link back to them, which is worth nothing on the day the homeserver's
# media store is not there any more. So the compile keeps the file too,
# in the archive the wiki serves, and the page embeds what it kept.

# What `stack.media` calls each kind of message the compiler
# recognises. `text` is absent on purpose: it carries no file.
_ARCHIVE_KIND = {"voice": "audio", "image": "image",
                 "video": "video", "file": "file"}


async def _archive_media(messages, events, *, session, homeserver, token,
                         room_id: str) -> "dict[str, str]":
    """Keep the file behind every upload; return where each one landed.

    Keyed by artifact id: the event id of an upload, and for each file of
    a gallery its event id and place (`messages.gallery_artifact_id`), in the
    site-rooted form a page embeds, which is what `pages_for` takes. The value is the copy meant to be
    shown: the AAC version of a recording, the bounded version of a
    photograph, the original for everything else.

    Nothing here is allowed to stop a compile. An unreachable
    homeserver, a full disk, a container without ffmpeg: each costs an
    entry its file and nothing more. The entry is left out of the map
    and its page renders the way it did before any of this existed.

    Bytes already in the archive are not fetched again. A compile walks
    the room's whole history every night, and re-downloading years of
    photographs to write files that are already on disk would make
    every night cost what the first one did.
    """
    root = media.open_archive()
    if root is None:
        err("  no media archive configured, keeping no originals")
        return {}

    raw = {ev.get("event_id"): ev for ev in events}
    paths: "dict[str, str]" = {}
    fetched = 0
    for msg in messages:
        event = raw.get(msg.event_id) or {}
        for file in msg.files:
            kind = _ARCHIVE_KIND.get(file.kind, "")
            if not kind:
                continue
            kept = await _keep_file(root, msg, file, event, kind=kind, session=session,
                                    homeserver=homeserver, token=token, room_id=room_id)
            if kept is None:
                continue
            paths[file.artifact_id], new = kept
            fetched += new

    if fetched:
        err(f"  kept {fetched} original(s)")
    return paths


async def _keep_file(root, msg, file, event: dict, *, kind: str, session, homeserver,
                     token, room_id: str) -> "tuple[str, bool] | None":
    """The link of the copy of `file` a page shows, and whether it was
    fetched now; None when it is not in the archive and cannot be kept."""
    content = event.get("content") or {}
    # A gallery's items carry their own name and type; an upload
    # carries them in the event.
    filename = file.filename or content.get("filename") or content.get("body") or ""
    mime = file.mime or (content.get("info") or {}).get("mimetype") or ""
    ext = media.extension_for(filename, mime)
    # Filed under when it arrived rather than under the date its
    # entry ends up carrying: the archive indexes events, and the
    # record beside each file states that same moment. The page
    # addresses the file by path, so the two need not agree.
    when = datetime.fromtimestamp(msg.ts / 1000, timezone.utc)

    link = media.kept(root, file.artifact_id, ext=ext, when=when)
    new = not link
    if new:
        # Logged before the fetch, not after. A first compile over a
        # room with years of photographs in it is minutes of
        # downloading, and a command that prints nothing for that
        # long is indistinguishable from one that has hung.
        err(f"  keeping {filename or file.artifact_id}")
        data = await download(session, homeserver, token, file.url)
        if not data:
            return None
        link = media.keep(
            root, file.artifact_id, data, ext=ext, when=when, kind=kind,
            source="diary", mime=mime, filename=filename,
            room_id=room_id, sender=event.get("sender") or "",
        )
        if not link:
            return None
    return media.derive(root, file.artifact_id, ext=ext, when=when, kind=kind) or link, new


async def keep_files(messages, events, *, session, homeserver: str, token: str,
                     room_id: str):
    """The messages with the day their photos were taken, and the files kept
    for them (artifact id to the link in the media store).

    Runs before anything is read or interpreted, so every later step sees
    the day a photo was taken (`with_capture_days`).
    """
    kept = await _archive_media(messages, events, session=session, homeserver=homeserver,
                                token=token, room_id=room_id)
    return with_capture_days(messages, media_dates.capture_days(media.open_archive(), kept)), kept


# ── The room the diary is compiled from ───────────────────────────────
#
# A card names the messages it was made from by their event ids, not the
# room (ADR-018 §3). The pages link each entry to its message in the
# room, so the compile keeps the room's id beside its other state, and
# `diary pages` and `diary correct`, which read no room, take it from
# there.

_ROOM_FILE = "room.json"


def remember_room(room_id: str) -> None:
    """Keep the id of the room the compile read, for the pages built without it."""
    if not room_id:
        return
    path = diary_store.state_dir() / _ROOM_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"room_id": room_id}) + "\n", encoding="utf-8")
    except OSError as e:
        err(f"  could not keep the room's id: {e}")


def remembered_room() -> str:
    """The id of the room the last compile read; "" before the first."""
    try:
        loaded = json.loads((diary_store.state_dir() / _ROOM_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(loaded.get("room_id") or "") if isinstance(loaded, dict) else ""
