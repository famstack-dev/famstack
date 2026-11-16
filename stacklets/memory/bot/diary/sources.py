"""A diary card's sources: one per message and reply, and the files they show.

    sources_of_parts([Part(photo), Part(reply, reply=True)], media=kept)
    media_of(cards)                    # event or gallery artifact id -> kept file
    files_by_event(event_ids, paths)   # [(event id, path), ...]

A card keeps one source per message of its entry and per file of a
gallery (ADR-018 §2). The archive names every file after the event it
came from (`stack.media.safe_name`), a gallery's files after the event
and their place, so a card lists only its paths and the event of each
file follows from its name.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from stack.cards.sources import ORIGINAL, REPLY, UNKNOWN, Source, time_of_ms
from stack.media import safe_name

from .messages import gallery_artifact_id

# A gallery's file stem: the event's stem and the file's place.
_PLACE = re.compile(r"^(?P<event>.+)-(?P<place>\d+)$")


def media_of(cards) -> "dict[str, str]":
    """The event-id-to-file map `pages_for` takes, from cards.

    The archive names every file, and every derived copy of it, after
    the event it came from (`stack.media.safe_name`), so a card needs
    to list only its paths. A gallery's files are named after the event
    and their place (`gallery_artifact_id`), and the map holds them
    under those names.
    """
    out: dict[str, str] = {}
    for card in cards:
        for event_id, path in files_by_event(card.event_ids, card.media):
            stem = Path(path).stem.split(".", 1)[0]
            match = _PLACE.match(stem)
            if match and match["event"] != safe_name(event_id):
                match = None
            out[gallery_artifact_id(event_id, int(match["place"])) if match
                else event_id] = path
    return out


def files_by_event(event_ids, paths) -> list[tuple[str, str]]:
    """Each archived file in `paths` with the event it was named after; a
    file named after none of `event_ids` is left out."""
    stems = {safe_name(e): e for e in event_ids}
    found = []
    for path in paths:
        stem = Path(path).stem.split(".", 1)[0]
        if (event_id := stems.get(stem)) is not None:
            found.append((event_id, path))
        elif (match := _PLACE.match(stem)) and match["event"] in stems:
            found.append((stems[match["event"]], path))
    return found


# ── The sources ───────────────────────────────────────────────────────
#
# A source's quote is what the card shows of one message: its file's
# embed (a link for a file that is no recording, photo or video), then
# its words. A gallery is one source per file, all with the gallery's
# event id, and its caption is in the first file's quote.

_EMBEDDED = ("voice", "image", "video")

def sources_of_parts(parts, *, media: "dict[str, str]",
                     media_sha: Callable[[str], str] = lambda link: "") -> list[Source]:
    """One source per message of an entry and per file of a gallery, in order.

    A part's file is its archived copy in `media` (by artifact id); a file
    the archive did not keep is a source without an embed. Its time is
    when the message reached the room; its `media` the hash the media
    store recorded (`media_sha`).

        sources_of_parts([Part(photo), Part(line), Part(reply, reply=True)], media=kept)
        # [Source('original', 'homer', …, 'image', '$p1', quote='![[/media/…/p1.jpg]]'),
        #  Source('original', 'homer', …, 'text', '$t1', quote='Grampa visited us'),
        #  Source('reply', 'marge', …, 'text', '$r1', quote='So cute!')]
    """
    sources: list[Source] = []
    for part in parts:
        message = part.message
        role = REPLY if part.reply else ORIGINAL
        time = sources_time(message.ts)
        words = message.body.strip()
        files = message.files or (None,)
        for place, file in enumerate(files):
            link = media.get(file.artifact_id, "") if file is not None else ""
            kind = file.kind if file is not None else message.kind
            sha = media_sha(link) if link else ""
            sources.append(Source(
                role, message.sender, time, "chat", kind, message.event_id,
                quote=quote_of(link, kind, words if place == 0 else ""),
                media=f"sha256:{sha}" if sha else "",
                reconstructed=time == UNKNOWN,
                duration_ms=file.duration_ms if file is not None else None))
    return sources


def sources_time(ms) -> str:
    """A room's time (epoch milliseconds) as a source's time; `unknown` for none."""
    return time_of_ms(ms) or UNKNOWN


def quote_of(link: str, kind: str, words: str) -> str:
    """What a source's quote holds: its file's embed or link, then its words."""
    shown = ""
    if link:
        shown = f"![[{link}]]" if kind in _EMBEDDED else f"[{Path(link).name}]({link})"
    return "\n".join(part for part in (shown, words) if part)
