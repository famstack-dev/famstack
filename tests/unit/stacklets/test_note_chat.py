"""A note's message and thread, read from the chat as the archivist.

The card records the event a note was filed from but not its room, so
the room is the one of the archivist's rooms that has the event, and the
thread is the `m.thread` relations under it. Synapse is a real HTTP
server here (`pytest-httpserver`) answering the client API.
"""

from __future__ import annotations

import sys
from pathlib import Path

import aiohttp
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot" / "cli"))

from _chat import read_thread  # noqa: E402

EVENT = "$n1:simpson"
MESSAGE = {"type": "m.room.message", "event_id": EVENT, "sender": "@marge:simpson",
           "content": {"msgtype": "m.text", "body": "homer calls the hotel thu"}}
REPLY = {"type": "m.room.message", "event_id": "$r1", "sender": "@bart:simpson",
         "content": {"msgtype": "m.text", "body": "and my swimsuit"}}


def _synapse(httpserver, *, has_event=True):
    httpserver.expect_request("/_matrix/client/v3/joined_rooms").respond_with_json(
        {"joined_rooms": ["!other:simpson", "!trip:simpson"]})
    httpserver.expect_request(f"/_matrix/client/v3/rooms/!other:simpson/event/{EVENT}").respond_with_json(
        {"errcode": "M_NOT_FOUND"}, status=404)
    httpserver.expect_request(f"/_matrix/client/v3/rooms/!trip:simpson/event/{EVENT}").respond_with_json(
        MESSAGE if has_event else {"errcode": "M_NOT_FOUND"}, status=200 if has_event else 404)
    httpserver.expect_request(
        f"/_matrix/client/v1/rooms/!trip:simpson/relations/{EVENT}/m.thread").respond_with_json(
        {"chunk": [REPLY]})


async def _read(httpserver):
    async with aiohttp.ClientSession() as http:
        return await read_thread(http, httpserver.url_for("").rstrip("/"), "token", EVENT)


@pytest.mark.asyncio
async def test_the_message_is_found_in_the_room_that_has_it_with_its_thread(httpserver):
    _synapse(httpserver)
    found = await _read(httpserver)
    assert found is not None
    assert (found.message, found.replies) == (MESSAGE, [REPLY])
    assert all(r.headers.get("Authorization") == "Bearer token" for r, _ in httpserver.log)


@pytest.mark.asyncio
async def test_a_message_no_room_has_is_none(httpserver):
    _synapse(httpserver, has_event=False)
    assert await _read(httpserver) is None
