"""Which earlier photo goes with this message to the agent?

Many chat apps send a photo without a caption, and people explain it in
the next message:

    Homer:  [photo of a handwritten list]
    Homer:  Stacky, put this on the shopping list

In a room with several people nanobot decides from the event alone
whether a message is for the agent. The photo carries no name, so the
gate drops it, and the words reach the agent without the photo they are
about. A photo whose caption names the agent already works; this module
connects the photo sent first.

THE RULE

The same person's most recent unaddressed photo in the same room, from
the last `WINDOW_SECONDS`, goes with the next message of that person that
reaches the agent, once. Another person's photo is never attached: the
words "put this on the list" refer to what the speaker just sent. Two
photos: the newer one, the one the words most likely follow. A photo
that reached the agent on its own (a caption with the name, a reply in
the agent's thread, a direct chat) is never recorded, so it is never
attached again.

CHAT TIME

A photo's age is the time between the photo and the words, as the chat
server stamped them (each event's `server_timestamp`), not the time the
agent handled them. After a slow sync or a restart of the chat
connection, the agent handles a batch of events at once; the server's
stamps still say how far apart they were sent, so a delay on the
agent's side cannot stretch the window. An event without a stamp is
neither kept nor takes a photo.

WHAT IS KEPT, AND WHERE

Only the Matrix event of the newest unaddressed photo per person and
room, in memory, for `WINDOW_SECONDS`. The file is downloaded only when
words that address the agent take it, so a photo nobody refers to is
never downloaded for the model. A restart forgets everything; that
loses at most one photo sent in the minutes before it.

The record needs to know whether nanobot's gate let a photo through,
and asking the gate a second time is not safe: the open-question rule
answers yes once and then no. So the photo's own way through nanobot
says it: `arriving` before nanobot sees the photo, `reached_agent` when
nanobot hands a message to the agent, and `arrived` afterwards keeps
the photo only when it did not reach the agent.

`sitecustomize.py` wires this into nanobot's Matrix channel
(`_on_media_message`, `_handle_message`). This module holds the
decisions and the record, with no nanobot import, so its tests need no
channel.
"""

from __future__ import annotations

from typing import Any

# How long an unaddressed photo waits for the words about it. Long enough
# to type a sentence after the upload finishes, short enough that "put
# this on the list" an hour later does not pick up an old photo.
WINDOW_SECONDS = 5 * 60

# The Matrix message type of a photo or any other picture.
IMAGE_MSGTYPE = "m.image"


def is_photo(event: Any) -> bool:
    """True for an image message, the kind a phone sends for a photo."""
    source = getattr(event, "source", None)
    content = source.get("content") if isinstance(source, dict) else None
    return isinstance(content, dict) and content.get("msgtype") == IMAGE_MSGTYPE


def sent_at(event: Any) -> float | None:
    """When the chat server received the event, in seconds; None without a stamp.

    nio carries the stamp as `server_timestamp`, in milliseconds.
    """
    stamp = getattr(event, "server_timestamp", None)
    return stamp / 1000 if isinstance(stamp, int) else None


def carries_a_photo(metadata: dict | None) -> bool:
    """True when the message handed to the agent is itself a photo.

    nanobot lists a media message's download in `metadata["attachments"]`;
    such a message brings its own picture and takes no earlier one.
    """
    attachments = (metadata or {}).get("attachments") or []
    return any(isinstance(a, dict) and a.get("type") == "image" for a in attachments)


def with_photo(content: str, marker: str) -> str:
    """The agent's text for words that take an earlier photo.

    The same shape nanobot builds for a photo with that caption: the words,
    then the attachment's marker on its own line. The model reads the
    picture from the media list; a model without vision gets nanobot's
    placeholder for it, as for a captioned photo.
    """
    return f"{content}\n{marker}" if content and marker else content or marker


class RecentPhotos:
    """The newest unaddressed photo per person and room, for a few minutes.

    A key is the room and the sender. A photo is whatever the caller needs
    to download it later (nanobot's room and event); this class never looks
    inside it. Times are chat time in seconds (`sent_at`): when the photo
    and the words were sent.

        photos = RecentPhotos()
        photos.arriving("$photo")                    # before nanobot's gate
        photos.arrived("$photo", "!family", "@homer:simpson", photo, at=10.0)
        photos.take("!family", "@homer:simpson", at=40.0)   # -> photo, once
    """

    def __init__(self, window: float = WINDOW_SECONDS) -> None:
        self._window = window
        self._photos: dict[tuple[str, str], tuple[float, Any]] = {}
        self._in_flight: set[str] = set()

    def __len__(self) -> int:
        """How many photos wait for words."""
        return len(self._photos)

    # ── a photo's way through nanobot ────────────────────────────────────

    def arriving(self, event_id: str) -> None:
        """A photo is about to meet nanobot's gate."""
        if event_id:
            self._in_flight.add(event_id)

    def reached_agent(self, event_id: str) -> None:
        """nanobot handed this message to the agent; if it is a photo, it is addressed."""
        self._in_flight.discard(event_id)

    def arrived(self, event_id: str, room_id: str, sender: str, photo: Any, *, at: float) -> bool:
        """nanobot is done with the photo; keep it if it did not reach the agent.

        Returns True when the photo was kept.
        """
        if event_id not in self._in_flight:
            return False
        self._in_flight.discard(event_id)
        self.saw(room_id, sender, photo, at=at)
        return True

    # ── the record ───────────────────────────────────────────────────────

    def saw(self, room_id: str, sender: str, photo: Any, *, at: float) -> None:
        """Keep an unaddressed photo sent at `at`; it replaces the sender's earlier one in that room."""
        self._forget_before(at - self._window)
        self._photos[(room_id, sender)] = (at, photo)

    def take(self, room_id: str, sender: str, *, at: float) -> Any | None:
        """For words sent at `at`: the sender's photo in that room from within the window, once; None if there is none."""
        self._forget_before(at - self._window)
        seen = self._photos.pop((room_id, sender), None)
        return seen[1] if seen else None

    def _forget_before(self, cutoff: float) -> None:
        for key in [k for k, (seen_at, _) in self._photos.items() if seen_at < cutoff]:
            del self._photos[key]
