"""A topic's entries: its newest notes in full, their messages one tap away.

    entries.section(answer, context, media=records, names=names, route="managed")   # "Entries 4", "" for no note
    entries.media_paths(answer)                                    # the media store paths the section draws

An entry is a note read from top to bottom on the topic's page: who filed
it, the day, ✦ when the AI wrote its text (`memory_ui.entry.made.ai_made`,
the card page's rule, from the sidecar's `made`), named for the AI's
`route` (`memory_ui.ai_route`), in the card page's words (`ai_hint`), its title, its text as it is and the photos sent with it. Its foot folds the
messages it was filed from under "show the original messages", drawn as the
card page draws them (`memory_ui.entry`), and links the card's page. The
answer holds the newest ten (`entries`) on the first part only; older notes
of the part the page shows stay rows, as the topic's cards were before.

A reply in the note's thread that came after the AI wrote the text is
counted above the text: the text stays as written and the reply is under
"Added by" in the folded messages. A reply before that moment is part of
the text, so it is not counted. A note filed as sent has no prompt template,
so it has no ✦ and no count: its text is the messages.

Why it does not read the card: the memory's answer carries each entry with
its sources, so the page asks nothing twice. The drawing of the sources is
the package `memory_ui.entry`, which knows no topic.

Pure: no I/O. `media` is the media store's record of each path
(`Reads.kept`), `names` maps a person id to the name a reader knows.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from html import escape

from memory_home import sections
from memory_home.frame import avatar
from memory_home.items import Context, person_name, row
from memory_home.words import words
from memory_ui.addresses import card_address
from memory_ui.entry import draw, media_paths as card_media_paths, sections_of
from memory_ui.entry.made import ai_hint, ai_made, mark
from memory_ui.text import to_html

__all__ = ["section", "media_paths", "without_notes"]

# The sections of a note that show with its text; the other messages wait in the fold.
PHOTO_KINDS = ("image", "images")


def section(answer: dict, context: Context, *, media: Mapping[str, Mapping | None],
            names: Mapping[str, str], route: str = "") -> str:
    """The "Entries" section: the entries in full, then the older notes of
    the part as rows; "" for a topic with no note on this part. Its count
    is the topic's notes. `route` is the AI's, which ✦ names (`memory_ui.ai_route.of`)."""
    found = answer.get("entries") or []
    older = _older(answer, found)
    if not found and not older:
        return ""
    count = (answer.get("counts") or {}).get("note") or len(found) + len(older)
    hint = ai_hint(route, language=context.language)
    body = "".join(_entry(e, context, media, names, hint) for e in found)
    body += sections.rows(row(i, context) for i in older) if older else ""
    return sections.section("entries", context, body, count)


def media_paths(answer: dict) -> list[str]:
    """The media store paths of the entries' files, for the page to read their records."""
    return [p for e in answer.get("entries") or [] for p in card_media_paths(_card_of(e))]


def without_notes(answer: dict) -> dict:
    """The answer with the notes taken out of its cards list: the entries section draws them."""
    part = answer.get("cards") or {}
    notes = (answer.get("counts") or {}).get("note", 0)
    items = [i for i in part.get("items", []) if i.get("type") != "note"]
    return {**answer, "cards": {**part, "items": items, "count": max(part.get("count", 0) - notes, 0)}}


# ── One entry ──────────────────────────────────────────────────────────────

def _entry(e: dict, context: Context, media: Mapping[str, Mapping | None], names: Mapping[str, str],
           hint: str) -> str:
    w = words(context.language)
    card = _card_of(e)
    parts = sections_of(card)
    who = e.get("sender") or ""
    when = context.day_words(e.get("day"))
    ai = mark(hint) if ai_made(e) else ""
    photos = draw([p for p in parts if p.kind in PHOTO_KINDS], media=media, names=names,
                  language=context.language)
    fold = (f'<details class="entry-messages"><summary class="show-messages">{escape(w["entry_show"])}'
            f'</summary>{draw(parts, media=media, names=names, language=context.language)}</details>'
            if parts else "")
    return (f'<article class="entry" data-entry="{escape(e["id"])}">'
            f'<header class="entry-head">{avatar(who, person_name(who, context)) if who else ""}'
            f'<span class="entry-who">{escape(person_name(who, context)) if who else ""}</span>'
            f'<span class="entry-date">{escape(when)}</span>{ai}</header>'
            f'<h3 class="entry-title"><a class="internal" href="{escape(card_address(e["id"]))}">'
            f'{escape(e.get("title") or e["id"])}</a></h3>'
            f'{_later(e, w)}<div class="entry-text">{to_html(e.get("text") or "")}{photos}</div>'
            f'<div class="entry-foot">{fold}<a class="entry-open internal" '
            f'href="{escape(card_address(e["id"]))}">{escape(w["entry_open"])}</a></div></article>')


def _later(e: dict, w: dict) -> str:
    """"↩ 1 message added after this text", "" when no reply came after the AI wrote it."""
    n = _replies_after(e)
    if not n:
        return ""
    one, many = w["entry_later"]
    return f'<p class="entry-later">{escape((one if n == 1 else many).format(n=n))}</p>'


def _replies_after(e: dict) -> int:
    """How many replies came after the model wrote the text; 0 for a note it did not write."""
    made = _moment((e.get("made") or {}).get("at"))
    if not ai_made(e) or made is None:
        return 0
    return sum(1 for s in e.get("sources") or []
               if s.get("role") == "reply" and (at := _moment(s.get("time"))) is not None and at > made)


def _moment(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _card_of(e: dict) -> dict:
    """The entry as the card shape `memory_ui.entry` reads its sections from."""
    return {"type": "note", "id": e.get("id"), "sources": e.get("sources") or []}


def _older(answer: dict, found: list[dict]) -> list[dict]:
    """The notes of the part the page shows that are not drawn in full."""
    drawn = {e["id"] for e in found}
    return [i for i in (answer.get("cards") or {}).get("items", [])
            if i.get("type") == "note" and i.get("id") not in drawn]
