"""A capture: what a person hands the memory to keep, as an intake gives it to the filing core.

    origin = Origin(channel="chat", person="marge", account="@marge:simpson",
                    capture_id="$grill", sent_at="2026-11-22T08:20:00+00:00")
    capture = Capture(kind="note", content=words, origin=origin, source_kind="text")
    original_of(capture)
    # Source(role='original', person='marge', time='2026-11-22T08:20:00+00:00',
    #        channel='chat', kind='text', id='$grill', quote='The grill needs ...')

An intake (the archivist in a room, `stack memory capture` on the command
line) decides that a capture is complete and builds it; the core
(`stack.filing.file`) files it. A capture holds what was sent and where
it came from, never a room or a Matrix event: the intake names the
channel (`chat`, `cli`), the person and the id it keeps, and the core
never reads a channel from the shape of an id.

A correction files a capture again as the same card (`Refile`): on the
path it has, by the person who corrected it, with the card's own tags
and a person's field edits kept.

Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from ..cards.fields import FieldOp
from ..cards.sources import ORIGINAL, Source, new_id, now

__all__ = ["Capture", "Content", "KeptTags", "Origin", "Refile", "original_of"]


class Content(Protocol):
    """What was read out of a capture: its words, a title hint and the link it names.

        content.text, content.title_hint, content.source_uri
        # ('The grill needs new charcoal ...', None, None)
    """

    text: str
    title_hint: str | None
    source_uri: str | None


class KeptTags(Protocol):
    """A refiled card's own tags, of which the reading was shown only some:
    `restored(answered)` puts back the ones it was not shown.

        own.restored(["Camper Van", "van"])   # ['surprise-party', 'Camper Van', 'van']
    """

    def restored(self, answered: list[str]) -> list[str]: ...


@dataclass(frozen=True)
class Origin:
    """Where a capture came from, as its intake names it.

    ``channel`` is the intake's (`chat`, `cli`). ``person`` is the sender's
    person id, the one a source and a card's `filed_by` keep. ``account`` is
    who the commit is by, as the curator names them (a chat account, or the
    person id). ``capture_id`` is the id the intake keeps for the capture,
    None when it has none and the core makes one (`new_id`). ``sent_at``
    is when it was sent; now when "".

        Origin(channel="cli", person="homer", account="@homer:simpson")
    """

    channel: str
    person: str
    account: str
    capture_id: str | None = None
    sent_at: str = ""


@dataclass(frozen=True)
class Capture:
    """One capture as the core files it.

    ``kind`` is the card it becomes (`note`, `bookmark`, `email`);
    ``content`` what was read out of it; ``source_kind`` what was sent
    (`text`, `voice`, `image`, `file`). ``kept_media`` is the archived copy
    of an upload, ``caption`` what the sender wrote with it, ``replies``
    the `(author, words)` a bookmark keeps from its thread, ``media_hash``
    the hash of an upload's bytes. ``sources`` are a refiled note's; a new
    note's one source is made from the capture (`original_of`).
    ``default_person`` says whether
    the sender is who a card naming nobody is about (not for an email,
    whose sender is a bot). ``email`` is an email's per-message identity
    (`message_id`, `from_addr`, `from_name`). ``captured_at`` is the card's
    day; today when None.

        Capture(kind="bookmark", content=page, origin=origin)
    """

    kind: str
    content: Content
    origin: Origin
    source_kind: str = "text"
    kept_media: dict | None = None
    caption: str = ""
    replies: "list[tuple[str, str]] | tuple" = ()
    media_hash: str = ""
    sources: "Sequence[Source] | None" = None
    default_person: bool = True
    email: dict | None = None
    captured_at: str | None = None


@dataclass(frozen=True)
class Refile:
    """A correction of a filed capture: it files again as the same card.

    ``existing_path`` is where the card is; it is rewritten there.
    ``by`` is who corrected it (the commit's author). ``own_tags`` are the
    card's own tags as the correction's room may see them, put back where
    the reading was not shown them; ``ops`` a person's field edits,
    applied again; ``requested_by`` who asked for this interpretation;
    ``words`` what the person wrote, which an email's correction reads;
    ``people`` who the card is about now, kept when the correction's
    answer names nobody.

        Refile(existing_path="marge/notes/2026/11/grill-0c401e.md", by="@marge:simpson")
    """

    existing_path: str
    by: str
    own_tags: KeptTags | None = None
    ops: "Sequence[FieldOp]" = field(default_factory=tuple)
    requested_by: str = ""
    words: str = ""
    people: "Sequence[str]" = ()


def original_of(capture: Capture) -> Source:
    """A new note's original: what was sent, by whom, when, on which channel.

    The id is the one the intake keeps (a chat message's event id), else
    one of the channel's own (ADR-018 §3). A photo is embedded in its
    quote, a file linked, and either is followed by its text and the
    sender's caption. A voice message's words are its transcript.

        original_of(Capture(kind="note", content=words,
                            origin=Origin("cli", "homer", "@homer:simpson")))
        # Source(role='original', person='homer', channel='cli', id='cli:3f9a...', ...)
    """
    origin, kind = capture.origin, capture.source_kind
    kept = capture.kept_media or {}
    media_line = (f"![[{kept['embed']}]]" if kept.get("embed") else
                  f"[{kept.get('name') or 'original'}]({kept['original']})"
                  if kept.get("original") else "")
    text = capture.content.text if kind != "image" else ""
    quote = "\n\n".join(p for p in (media_line, text.strip(), capture.caption.strip()) if p)
    return Source(
        ORIGINAL, origin.person, origin.sent_at or now(), origin.channel, kind,
        origin.capture_id or new_id(origin.channel), quote=quote or "(no words)",
        media=capture.media_hash, text={"form": "transcript"} if kind == "voice" else {},
    )
