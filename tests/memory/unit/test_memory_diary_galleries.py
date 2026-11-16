"""A gallery message is one part of a diary entry.

Element X's "gallery messages" setting sends several photos and videos as
one Matrix event with one caption: `msgtype` `m.gallery` (or
`dm.filament.gallery` while the proposal is open), its files in
`itemtypes`. The diary reads such an event as one message with its files
in order, so a gallery makes an entry like a photo does.

The first test drives the compile as the admin does (`./stack memory
diary`), with the room's gallery on the stand-in Synapse and its files on
the stand-in media store, and reads the card the memory kept. The others
read the room through `resolve`, the step that turns events into
messages. The family is the Simpsons.
"""

# ruff: noqa: F811  (the imported fixtures are used as test parameters)
from __future__ import annotations

import re

import pytest

from stacklets.memory.bot.diary.entries import compile_entries
from stacklets.memory.bot.diary.grouping import group_room
from stacklets.memory.bot.diary.messages import resolve
from tests.memory.unit.test_memory_diary_compile import (  # noqa: F401  (fixtures)
    BASE_TS, ENTRIES, Room, bot_command, bot_runner, brain, diary, memory, model, room,
)

CAPTION = "Grampa at the pumpkin patch"


def _gallery(event_id: str, items: list[dict], body: str = CAPTION, *,
             msgtype: str = "dm.filament.gallery", ts: int = BASE_TS,
             sender: str = "@marge:simpson") -> dict:
    return {"type": "m.room.message", "event_id": event_id, "sender": sender,
            "origin_server_ts": ts,
            "content": {"msgtype": msgtype, "body": body, "itemtypes": items}}


def _photo(name: str, mxc: str) -> dict:
    return {"itemtype": "m.image", "body": name, "url": mxc,
            "info": {"mimetype": "image/jpeg"}}


def _video(name: str, mxc: str) -> dict:
    return {"itemtype": "m.video", "body": name, "url": mxc,
            "info": {"mimetype": "video/mp4", "duration": 4000}}


# ── Through the compile ──────────────────────────────────────────────────

def test_a_gallery_of_two_photos_compiles_to_one_entry_with_both_files_and_the_caption(
        memory, room, diary, httpserver, monkeypatch, tmp_path):
    """Given a gallery of two photos with a caption in the room, when the
    admin compiles the diary, then one card holds both photos, in the
    gallery's order, and the caption as its words."""
    monkeypatch.setenv("MEDIA_ARCHIVE_DIR", str(tmp_path / "archive"))
    for media_id, data in (("first", b"first photo"), ("second", b"second photo")):
        httpserver.expect_request(
            f"/_matrix/client/v1/media/download/simpson/{media_id}").respond_with_data(data)
    room.events.append(_gallery("$gallery:simpson", [
        _photo("one.jpg", "mxc://simpson/first"),
        _photo("two.jpg", "mxc://simpson/second")]))

    diary.runs()

    [card] = memory.pages(ENTRIES).values()
    embeds = re.findall(r"!\[\[(.+?)\]\]", card)
    assert len(embeds) == 2 and len(set(embeds)) == 2
    assert embeds[0].endswith("-1.jpg") and embeds[1].endswith("-2.jpg"), embeds
    assert CAPTION in card


# ── Through resolve ──────────────────────────────────────────────────────

def test_a_gallery_is_one_message_with_its_files_in_order():
    events = [_gallery("$g", [_photo("a.jpg", "mxc://s/a"), _photo("b.jpg", "mxc://s/b"),
                              _photo("c.jpg", "mxc://s/c")])]

    [message] = resolve(events)

    assert [f.url for f in message.files] == ["mxc://s/a", "mxc://s/b", "mxc://s/c"]
    assert message.kind == "image"
    assert message.body == CAPTION


def test_a_gallery_of_a_photo_and_a_video_has_both_and_the_first_files_kind():
    events = [_gallery("$g", [_photo("a.jpg", "mxc://s/a"), _video("b.mp4", "mxc://s/b")])]

    [message] = resolve(events)

    assert [f.kind for f in message.files] == ["image", "video"]
    assert message.kind == "image"
    assert message.duration_ms == 4000


def test_a_gallery_without_a_caption_has_files_and_no_words():
    events = [_gallery("$g", [_photo("a.jpg", "mxc://s/a")], body="")]

    [message] = resolve(events)

    assert len(message.files) == 1
    assert message.body == ""


@pytest.mark.parametrize("msgtype", ["m.gallery", "dm.filament.gallery"])
def test_the_stable_and_the_unstable_gallery_are_the_same_message(msgtype):
    events = [_gallery("$g", [_photo("a.jpg", "mxc://s/a")], msgtype=msgtype)]

    [message] = resolve(events)

    assert [f.url for f in message.files] == ["mxc://s/a"]


def test_an_edit_of_the_caption_counts():
    events = [_gallery("$g", [_photo("a.jpg", "mxc://s/a")]),
              {"type": "m.room.message", "event_id": "$e", "sender": "@marge:simpson",
               "origin_server_ts": BASE_TS + 1000,
               "content": {"msgtype": "m.text", "body": "* Grampa at the farm",
                           "m.new_content": {"msgtype": "m.text", "body": "Grampa at the farm"},
                           "m.relates_to": {"rel_type": "m.replace", "event_id": "$g"}}}]

    [message] = resolve(events)

    assert message.body == "Grampa at the farm"
    assert len(message.files) == 1


def test_a_reply_in_a_gallerys_thread_joins_its_entry():
    events = [_gallery("$g", [_photo("a.jpg", "mxc://s/a")]),
              {"type": "m.room.message", "event_id": "$r", "sender": "@homer:simpson",
               "origin_server_ts": BASE_TS + 60_000,
               "content": {"msgtype": "m.text", "body": "Great day!",
                           "m.relates_to": {"rel_type": "m.thread", "event_id": "$g"}}}]

    messages = resolve(events)
    entries = compile_entries(group_room(messages, {}), {})

    [entry] = entries
    assert entry.event_ids == ["$g", "$r"]


def test_a_message_that_is_not_a_gallery_keeps_its_one_file():
    events = [{"type": "m.room.message", "event_id": "$i", "sender": "@marge:simpson",
               "origin_server_ts": BASE_TS,
               "content": {"msgtype": "m.image", "body": "x.jpg", "url": "mxc://s/x"}}]

    [message] = resolve(events)

    assert [f.url for f in message.files] == ["mxc://s/x"]
    assert resolve([{"type": "m.room.message", "event_id": "$t",
                                  "sender": "@marge:simpson", "origin_server_ts": BASE_TS,
                                  "content": {"msgtype": "m.text", "body": "hi"}}])[0].files == ()
