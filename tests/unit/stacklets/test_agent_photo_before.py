"""A photo, then the words: the agent sees both.

Many chat apps send a photo without a caption, and people explain it in
the next message: a photo of a handwritten list, then "Stacky, put this
on the shopping list". In a room with several people the photo carries no
name, so nanobot's gate drops it, and the words reached the agent alone.

Now the same person's most recent unaddressed photo in that room, from the
last five minutes, goes to the model with the message that addresses the
agent, in the same form as a photo sent with that caption. Nothing else
changes: another person's photo, an older one, and a photo that went to
the agent on its own are never attached, and a photo nobody refers to is
never downloaded for the model.

The surface is the Matrix channel as nanobot 0.3.5 drives it (the stub in
conftest): `_on_media_message` and `_on_message` gate the event and hand
it to `_handle_message`, whose `media` list is what nanobot turns into
image blocks, or into its text placeholder for a model without vision.
"""

from __future__ import annotations

import asyncio
import sys
import types

from photo_before import WINDOW_SECONDS, RecentPhotos

FAMILY_ROOM = types.SimpleNamespace(room_id="!family:simpson", member_count=5)
DIRECT_CHAT = types.SimpleNamespace(room_id="!homer-stacky:simpson", member_count=2)
HOMER, MARGE = "@homer:simpson", "@marge:simpson"
SHOPPING = "Stacky, put this on the shopping list"


def _photo(event_id, *, sender=HOMER, caption="", thread_root=""):
    """An image event as nio delivers it; most apps send no caption."""
    content = {"msgtype": "m.image", "body": caption or "IMG_0412.jpg"}
    if thread_root:
        content["m.relates_to"] = {"rel_type": "m.thread", "event_id": thread_root}
    # Without a caption the body is the file name, as nio reports it.
    return types.SimpleNamespace(event_id=event_id, sender=sender, body=content["body"],
                                 pill_mention=False, source={"content": content})


def _said(text, *, event_id="$words", sender=HOMER):
    return types.SimpleNamespace(event_id=event_id, sender=sender, body=text,
                                 pill_mention=False,
                                 source={"content": {"msgtype": "m.text", "body": text}})


def _channel(nanobot, monkeypatch):
    monkeypatch.setenv("AGENT_NAME", "Stacky")
    return nanobot()["nanobot.channels.matrix.runtime"].MatrixChannel()


def _send(channel, *events, room=FAMILY_ROOM):
    """Deliver events to the channel in order, as the sync loop does."""
    async def _run():
        for event in events:
            if event.source["content"]["msgtype"] == "m.text":
                await channel._on_message(room, event)
            else:
                await channel._on_media_message(room, event)
    asyncio.run(_run())


class _Clock:
    """A clock that moves only when the test says so, in seconds."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _turn(channel, event_id):
    """What the agent was handed for one message."""
    (turn,) = [h for h in channel.handled if h["metadata"].get("event_id") == event_id]
    return turn


# ── 1. a photo, then the words ───────────────────────────────────────────

def test_the_words_after_a_photo_reach_the_agent_with_the_photo(nanobot, monkeypatch):
    """Given Homer's photo without a caption in the family room, when he
    writes "Stacky, put this on the shopping list", then the agent's turn
    carries that photo. Before this, the words came alone and the agent
    had nothing to put on the list."""
    channel = _channel(nanobot, monkeypatch)

    _send(channel, _photo("$list"), _said(SHOPPING))

    turn = _turn(channel, "$words")
    assert turn["media"] == ["/media/matrix/list.jpg"]
    assert channel.downloaded == ["$list"]


def test_the_agent_gets_what_a_captioned_photo_gives_it(nanobot, monkeypatch):
    """Given the same photo once with the words as its caption, once with
    the words after it, then the agent is handed the same text and media.
    A model without vision then gets nanobot's own placeholder for both."""
    captioned = _channel(nanobot, monkeypatch)
    _send(captioned, _photo("$list", caption=SHOPPING))
    then_words = _channel(nanobot, monkeypatch)
    _send(then_words, _photo("$list"), _said(SHOPPING))

    as_caption, as_words = _turn(captioned, "$list"), _turn(then_words, "$words")
    assert (as_words["content"], as_words["media"]) == (as_caption["content"], as_caption["media"])


# ── 2. another person's photo ────────────────────────────────────────────

def test_another_persons_photo_is_not_attached(nanobot, monkeypatch):
    """Given Marge's photo, when Homer addresses the agent, then the agent
    gets Homer's words alone, and Marge's photo is never downloaded."""
    channel = _channel(nanobot, monkeypatch)

    _send(channel, _photo("$marges", sender=MARGE), _said(SHOPPING))

    assert not _turn(channel, "$words")["media"]
    assert channel.downloaded == []


# ── 3. an older photo ────────────────────────────────────────────────────

def test_a_photo_older_than_the_window_is_not_attached(nanobot, monkeypatch):
    """Given Homer's photo, when his words to the agent come more than five
    minutes later, then the agent gets the words alone and the photo is
    never downloaded. Driven through the channel on the shim's own record,
    so the time a photo is kept and the time words ask for it are read the
    way the agent reads them."""
    channel = _channel(nanobot, monkeypatch)
    clock = _Clock()
    monkeypatch.setattr(sys.modules["sitecustomize"]._recent_photos, "clock", clock)

    _send(channel, _photo("$list"))
    clock.now = WINDOW_SECONDS + 1
    _send(channel, _said(SHOPPING))

    assert not _turn(channel, "$words")["media"]
    assert channel.downloaded == []


def test_a_photo_just_inside_the_window_is_attached():
    """Given a photo seen just under five minutes before the words, then it
    is attached; one second past the window, it is not."""
    clock = _Clock()
    recent, old = RecentPhotos(clock=clock), RecentPhotos(clock=clock)
    recent.saw("!family", HOMER, "photo")
    old.saw("!family", HOMER, "photo")

    clock.now = WINDOW_SECONDS - 1
    assert recent.take("!family", HOMER) == "photo"
    clock.now = WINDOW_SECONDS + 1
    assert old.take("!family", HOMER) is None


# ── 4. two photos ────────────────────────────────────────────────────────

def test_of_two_photos_the_most_recent_is_attached(nanobot, monkeypatch):
    """Given two photos by Homer, then the agent gets the second one only."""
    channel = _channel(nanobot, monkeypatch)

    _send(channel, _photo("$first"), _photo("$second"), _said(SHOPPING))

    assert _turn(channel, "$words")["media"] == ["/media/matrix/second.jpg"]
    assert channel.downloaded == ["$second"]


def test_a_photo_goes_with_one_message_only(nanobot, monkeypatch):
    """Given a photo and the words that took it, then the next message to
    the agent comes without it."""
    channel = _channel(nanobot, monkeypatch)

    _send(channel, _photo("$list"), _said(SHOPPING),
          _said("Stacky, and milk", event_id="$more"))

    assert not _turn(channel, "$more")["media"]


def test_words_for_someone_else_leave_the_photo_waiting(nanobot, monkeypatch):
    """Given a photo and a message to Marge in between, then the next
    message to the agent still gets the photo."""
    channel = _channel(nanobot, monkeypatch)

    _send(channel, _photo("$list"), _said("Marge, look", event_id="$aside"),
          _said(SHOPPING))

    assert _turn(channel, "$words")["media"] == ["/media/matrix/list.jpg"]


# ── 5. a photo already addressed ─────────────────────────────────────────

def test_a_captioned_photo_is_not_attached_again(nanobot, monkeypatch):
    """Given a photo whose caption names the agent, then it reaches the
    agent on its own, and the next message comes without it."""
    channel = _channel(nanobot, monkeypatch)

    _send(channel, _photo("$list", caption=SHOPPING),
          _said("Stacky, and milk", event_id="$more"))

    assert not _turn(channel, "$more")["media"]
    assert channel.downloaded == ["$list"]


def test_a_photo_in_the_agents_thread_is_not_attached_again(nanobot, monkeypatch):
    """Given a photo posted in a thread on the agent's message, then it
    reaches the agent as part of that conversation, and the next message
    comes without it."""
    channel = _channel(nanobot, monkeypatch)

    async def room_get_event(room_id, event_id):
        return types.SimpleNamespace(event=types.SimpleNamespace(sender=channel.config.user_id))
    channel.client.room_get_event = room_get_event

    _send(channel, _photo("$list", thread_root="$agents-answer"), _said(SHOPPING))

    assert _turn(channel, "$list")["media"] == ["/media/matrix/list.jpg"]
    assert not _turn(channel, "$words")["media"]
    assert channel.downloaded == ["$list"]


# ── 6. a direct chat ─────────────────────────────────────────────────────

def test_in_a_direct_chat_the_photo_goes_to_the_agent_at_once(nanobot, monkeypatch):
    """Given a direct chat, then nanobot hands the photo over itself, and
    the words after it come alone."""
    channel = _channel(nanobot, monkeypatch)

    _send(channel, _photo("$list"), _said(SHOPPING), room=DIRECT_CHAT)

    assert _turn(channel, "$list")["media"] == ["/media/matrix/list.jpg"]
    assert not _turn(channel, "$words")["media"]


# ── 7. the resting state ─────────────────────────────────────────────────

def test_a_photo_nobody_refers_to_is_never_downloaded(nanobot, monkeypatch):
    """Given a photo and no message to the agent, then nothing reaches the
    model and nothing is downloaded for it."""
    channel = _channel(nanobot, monkeypatch)

    _send(channel, _photo("$list"), _said("Marge, look", event_id="$aside"))

    assert channel.handled == []
    assert channel.downloaded == []


def test_a_restart_forgets_the_photo(nanobot, monkeypatch):
    """Given a photo before a restart, then the words after it come alone:
    the photos are kept in memory only."""
    before = _channel(nanobot, monkeypatch)
    _send(before, _photo("$list"))
    after = _channel(nanobot, monkeypatch)

    _send(after, _said(SHOPPING))

    assert not _turn(after, "$words")["media"]


def test_a_photo_replayed_at_startup_is_not_kept(nanobot, monkeypatch):
    """Given a photo from before the start that the sync replays, then it
    is not attached: it was sent long ago, whatever the clock says now."""
    channel = _channel(nanobot, monkeypatch)
    replayed = _photo("$list")
    replayed.pre_startup = True

    _send(channel, replayed, _said(SHOPPING))

    assert not _turn(channel, "$words")["media"]


def test_photos_past_the_window_are_forgotten():
    """Given photos older than the window, then the record holds none of
    them: nothing waits longer than five minutes."""
    clock = _Clock()
    photos = RecentPhotos(clock=clock)
    photos.saw("!family", HOMER, "old")
    clock.now = WINDOW_SECONDS + 1
    photos.saw("!family", MARGE, "newer")

    assert len(photos) == 1
