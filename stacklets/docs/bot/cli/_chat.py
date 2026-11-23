"""A note's message and its thread, read from the chat as the archivist.

A note's sidecar records the event it was filed from (its original's `id`) but
not the room. The archivist is in every room it files from, so the room is
the one of its rooms that has the event. The thread is the replies
posted under the message (`m.thread`), where the archivist answers a
filing and the family corrects it.

Best effort: a refile without the chat reads the card's copy of the
words and replies instead (`memory.note_card`), so every failure here
is a None and a line on stderr, never an error. `source_of` reads an
event as a note's source, the form the refile adds it in
(`stack.filing.refile.refile_sources`).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from urllib.parse import quote

import aiohttp

from stack.cards.sources import REPLY, UNKNOWN, Source, time_of_ms

from ._mirror import _DATA_DIR
from ._shared import err

# MicroBot writes `<bot id>.session.json` into the session dir the bot
# runner gives the archivist, which is this command's `_DATA_DIR`.
_SESSION_FILE = _DATA_DIR / "archivist-bot.session.json"
_HOMESERVER = os.environ.get("MATRIX_HOMESERVER", "http://stack-messages-synapse:8008")
# A note's thread is a filing reply and a handful of corrections; this
# many pages of a hundred is far past any of them.
_THREAD_PAGES = 5
# What a chat message sent as a file is, by its `msgtype`.
_ATTACHMENTS = {"m.image": "image", "m.video": "video", "m.audio": "voice", "m.file": "file"}
# The quote of the replied-to message a client puts above a reply's words.
_REPLY_FALLBACK = re.compile(r"\A(?:>[^\n]*\n)+\n?")


@dataclass(frozen=True)
class ChatThread:
    """The message a note was sent as, and the events of its thread in order."""

    message: dict
    replies: list[dict]


def source_of(event: dict, role: str) -> Source | None:
    """A Matrix message event as a note's source of `role`; None for anything else.

    A file's words are its caption: modern clients send the file's name in
    `filename` and the caption in `body`, and a client with no caption
    sends the name in both or `body` alone (as the archivist reads it when
    it files the upload, `text_utils.attachment_caption`). A reply's words
    are without the quote of the message it replies to.

        source_of({"type": "m.room.message", "event_id": "$r2", "sender": "@lisa:simpson",
                   "origin_server_ts": 1760999999000,
                   "content": {"msgtype": "m.text", "body": "me too"}}, REPLY).person   # 'lisa'
    """
    content = event.get("content") or {}
    if event.get("type") != "m.room.message" or not isinstance(content.get("body"), str):
        return None
    kind = _ATTACHMENTS.get(str(content.get("msgtype") or ""), "text")
    text = content["body"]
    if kind != "text":
        filename = content.get("filename")
        text = text if filename and text != filename else ""
    elif role == REPLY:
        text = _REPLY_FALLBACK.sub("", text)
    time = time_of_ms(event.get("origin_server_ts")) or UNKNOWN
    person = str(event.get("sender") or "").split(":")[0].lstrip("@").strip().lower()
    return Source(role, person, time, "chat", kind, str(event.get("event_id") or UNKNOWN),
                  quote=text.strip())


async def read_note_thread(event_id: str) -> ChatThread | None:
    """The message `event_id` and its thread, or None when the chat cannot say."""
    if not event_id.startswith("$"):
        return None
    try:
        token = json.loads(_SESSION_FILE.read_text())["access_token"]
    except (OSError, ValueError, KeyError):
        err("… the archivist has no Matrix session here; the card's copy is the source")
        return None
    try:
        async with aiohttp.ClientSession() as http:
            return await read_thread(http, _HOMESERVER, token, event_id)
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        err(f"… the chat could not be read ({e}); the card's copy is the source")
        return None


async def read_thread(http: aiohttp.ClientSession, homeserver: str, token: str,
                      event_id: str) -> ChatThread | None:
    """`event_id` and its thread, from the first joined room that has it."""
    auth = {"Authorization": f"Bearer {token}"}
    async with http.get(f"{homeserver}/_matrix/client/v3/joined_rooms", headers=auth) as resp:
        if resp.status != 200:
            return None
        rooms = (await resp.json()).get("joined_rooms") or []
    for room in rooms:
        base = f"{homeserver}/_matrix/client/v1/rooms/{quote(room)}"
        async with http.get(f"{homeserver}/_matrix/client/v3/rooms/{quote(room)}/event/"
                            f"{quote(event_id)}", headers=auth) as resp:
            if resp.status != 200:
                continue
            message = await resp.json()
        return ChatThread(message, await _thread(http, base, auth, event_id))
    return None


async def _thread(http, base: str, auth: dict, event_id: str) -> list[dict]:
    """The events in the thread under `event_id`, oldest first."""
    events: list[dict] = []
    params = {"dir": "f", "limit": "100"}
    for _ in range(_THREAD_PAGES):
        async with http.get(f"{base}/relations/{quote(event_id)}/m.thread",
                            headers=auth, params=params) as resp:
            if resp.status != 200:
                break
            page = await resp.json()
        events += page.get("chunk") or []
        if not page.get("next_batch"):
            break
        params = {**params, "from": page["next_batch"]}
    return events
