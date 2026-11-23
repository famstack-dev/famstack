"""A capture's original names the channel its intake chose, never one read off its id.

`stack.filing.capture.original_of` is the first source of a new note
(ADR-018 §3). The intake (the archivist in a room, `stack memory capture`)
says where the capture came from; an id that looks like a chat event's does
not make a command-line capture a chat message, nor the reverse.
"""

from dataclasses import dataclass

from stack.filing.capture import Capture, Origin, original_of


@dataclass
class Words:
    text: str
    title_hint: str | None = None
    source_uri: str | None = None


GRILL = Words("The grill needs new charcoal before Saturday.")


def test_a_command_line_capture_with_an_event_shaped_id_is_from_the_command_line():
    """Given a capture from the command line whose id starts with `$`,
    when its original is made, then its channel is `cli` and it keeps that id."""
    origin = Origin(channel="cli", person="homer", account="homer", capture_id="$looks-like-chat")
    original = original_of(Capture(kind="note", content=GRILL, origin=origin))
    assert (original.channel, original.id) == ("cli", "$looks-like-chat")


def test_a_chat_capture_without_an_id_is_from_the_chat_and_gets_one_of_its_own():
    """Given a capture from the chat with no id, when its original is made,
    then its channel is `chat` and its id is a new one of that channel."""
    origin = Origin(channel="chat", person="marge", account="@marge:simpson")
    original = original_of(Capture(kind="note", content=GRILL, origin=origin))
    assert original.channel == "chat" and original.id.startswith("chat:")


def test_the_original_quotes_the_words_and_the_caption_by_its_sender():
    """Given a note with a caption, when its original is made, then it is the
    sender's, at the time sent, quoting the words and then the caption."""
    origin = Origin(channel="chat", person="marge", account="@marge:simpson",
                    capture_id="$grill", sent_at="2026-11-22T08:20:00+00:00")
    original = original_of(Capture(kind="note", content=GRILL, origin=origin, caption="for the barbecue"))
    assert (original.person, original.time, original.quote) == (
        "marge", "2026-11-22T08:20:00+00:00",
        "The grill needs new charcoal before Saturday.\n\nfor the barbecue")
