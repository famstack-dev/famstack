"""dating: when a diary entry happened, and how the day was found.

    on, basis, sentence = date_for(group, readings)
    messages = with_capture_days(messages, {artifact_id: date(2026, 10, 31)})
    basis_text("taken")   # "dated from when the photo was taken"

Matrix records only when a message reached the server. A memo recorded
on a hike and sent that evening, or photos sent a week after they were
taken, would land on the wrong day if that were all the diary knew. So
an entry's day comes from the first of these that exists:

    named   a day its words name, typed or spoken          basis "spoken"
    taken   the earliest day its photos were taken         basis "taken"
    sent    the day its first message was sent             basis "sent"
            (a message of a phone's late queue: the day    basis "uncertain"
             it arrived, said to be unsure)

The day a photo was taken is its EXIF `DateTimeOriginal`, which the
compile reads from the copy it keeps (`stack.media_dates`). Many
clients strip EXIF before upload (Element X with "Optimise media", its
default), so a photo without one falls back to the day it was sent,
silently and exactly as before.

The basis is the word a card stores (`date_basis` in its sidecar); the
sentence for it is chrome and follows the language the pages render in,
which `texts.configure_language` selects here too.

Pure: reads no room, no file and no model. `entries.compile_entries` calls
`date_for` for each message group; the compile command calls
`with_capture_days` once the files are on disk.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stack.message_groups import MessageGroup

    from .messages import Message


# ── The sentences for each basis ──────────────────────────────────────

_BASIS = {
    "en": {
        "spoken": "dated from the spoken opening",
        "taken": "dated from when the photo was taken",
        "sent": "dated from when it was sent",
        "corrected": "dated by a correction from the family",
        "uncertain": ("arrived in a phone's late queue and names no day, so "
                      "this is the day it arrived, not when it happened"),
    },
    "de": {
        "spoken": "datiert nach dem gesprochenen Datum",
        "taken": "datiert nach dem Aufnahmetag des Fotos",
        "sent": "datiert nach dem Sendezeitpunkt",
        "corrected": "datiert nach einer Korrektur aus der Familie",
        "uncertain": ("kam verspätet mit anderen Nachrichten eines Telefons "
                      "an und nennt keinen Tag; eingeordnet am Tag der "
                      "Ankunft, nicht des Geschehens"),
    },
}

_texts = _BASIS["en"]


def configure_language(code: str) -> None:
    """Select the language of the basis sentences. Unknown codes keep English."""
    global _texts
    _texts = _BASIS.get((code or "").strip().lower()[:2], _BASIS["en"])


def basis_text(basis: str) -> str:
    """The sentence that says how a day was found, in the household language.

    An unknown basis reads as `sent`, the day a server heard about it.
    """
    return _texts.get(basis, _texts["sent"])


# ── The day ───────────────────────────────────────────────────────────


def parse_spoken_date(value: str | None) -> date | None:
    """An ISO date the classifier heard, or None if it heard nothing."""
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def date_for(group: "MessageGroup", readings) -> tuple[date, str, str]:
    """When an entry happened, its basis, and the sentence for the basis.

    `group` is a message group of the diary's messages, `readings` what
    the model read, by event id. The first that exists wins:

    1. A day the entry's words name: the first of its messages that names
       one (`group_room` keeps a second named day out of the group). It
       says when the thing happened rather than when a server heard of it.
    2. The earliest day its photos were taken (`Message.taken`). Also for
       a late queue: a photo's own day is not off, as its arrival is.
    3. The day its first message was sent, for a group sent live.
    4. For a group of a phone's flushed queue, the day it arrived, said to
       be uncertain. A confidently wrong date in a family diary is worse
       than an honest gap.
    """
    messages: "tuple[Message, ...]" = group.messages  # type: ignore[assignment]
    for message in messages:
        reading = readings.get(message.event_id)
        spoken = parse_spoken_date(reading.spoken_date if reading else None)
        if spoken is not None:
            return spoken, "spoken", basis_text("spoken")
    taken = [m.taken for m in messages if m.taken is not None]
    if taken:
        return min(taken), "taken", basis_text("taken")
    sent_on = messages[0].sent_on
    if group.queued:
        return sent_on, "uncertain", basis_text("uncertain")
    return sent_on, "sent", basis_text("sent")


def with_capture_days(messages, capture_days: "dict[str, date | None]") -> list:
    """The messages, each with the earliest capture day of its files.

    `capture_days` maps a file's artifact id to the day it was taken, or
    None for a file that carries none. A day after the message was sent
    is a wrong camera clock, since a photo cannot be taken after it was
    sent, and is ignored. A message none of whose files has a day comes
    back unchanged, so its entry is dated by the day it was sent.
    """
    out = []
    for message in messages:
        days = [d for f in message.files
                if (d := capture_days.get(f.artifact_id)) and d <= message.sent_on]
        out.append(replace(message, taken=min(days)) if days else message)
    return out
