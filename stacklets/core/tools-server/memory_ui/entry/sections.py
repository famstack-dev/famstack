"""Reading a card's sources into the sections its page shows.

A card made from messages (a diary entry, a note filed from a chat) keeps
each message as a source (ADR-018). The cards API gives them as
`card["sources"]`: in the order sent, each with who sent it, when, its
kind, its length and its quote (the file's embed, then the words that
came with it). This module turns them into sections; `view.py` draws
them, `facts.py` says what they hold.

    sections = sections_of(card)
    [s.kind for s in sections]            # ['voice', 'images', 'added']
    media_paths(card)                     # ['2026/11/v.m4a', ...], for the media store

| Section | Made from |
|---|---|
| text | a message of words |
| voice | a recording; recordings in a row are one section |
| image | one photo and its caption |
| images | 2 to 10 photos in a row with no words of their own |
| video | a video |
| file | any other file |
| added | a reply in the card's thread, after filing |

Worked example: a note filed from three messages, a photo with a caption,
two photos without words, and a reply of Marge's the next day. Its
sections are an Image section with its caption, an Images section of the
two (a mosaic), and an Added section. The card's text is never parsed
for them. A `note` source (a correction given to the model) is no
section. A reply whose time a migrated card could not record (`unknown`)
says who added it, not when.

Why a card of either type: one view of the sources, not one per type
(a note must not grow a parallel reader). Pure: no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace

# The card types whose pages show their sources as sections.
SOURCE_TYPES = ("diary", "note")

# The kinds a section has; the drawing module gives each its icon.
SECTION_KINDS = ("text", "voice", "image", "images", "video", "file")

__all__ = ["Medium", "Section", "SOURCE_TYPES", "SECTION_KINDS", "MOSAIC_MAX", "sections_of",
           "media_paths", "date_basis", "sources_of_card", "medium_and_words"]


@dataclass(frozen=True)
class Medium:
    """One file of an entry: where the media store keeps it, and its kind
    ("voice", "image", "video" or "file").

        Medium("2026/11/GrandmaVoiceEvent.m4a", "voice")
    """

    path: str
    kind: str


@dataclass(frozen=True)
class Section:
    """One section of an entry: its kind (a row of the table above), its
    media, the family's words in it, a recording's or a video's length in
    seconds (0 when the card does not say), and for a reply who added it
    and when (`at`, ISO 8601; "" when nobody recorded it).

        Section("voice", (Medium("2026/11/v.m4a", "voice"),), "Grandma is here!", 72)
        Section("added", words="She forgot her umbrella again.", by="homer",
                at="2026-11-11T08:30:00+00:00")
    """

    kind: str
    media: tuple[Medium, ...] = ()
    words: str = ""
    seconds: int = 0
    by: str = ""
    at: str = ""


def sections_of(card: Mapping) -> list[Section]:
    """The sections of a diary or note card, in the order its messages were
    sent, from its sources. [] for any other card, or one with no sources.

        >>> card = {"type": "diary", "sources": [
        ...     {"role": "original", "kind": "image", "quote": "![[/media/2026/08/a.png]]\\nAt the lake."},
        ...     {"role": "original", "kind": "image", "quote": "![[/media/2026/08/b.png]]"},
        ...     {"role": "original", "kind": "file", "quote": "[c.pdf](/media/2026/08/c.pdf)"},
        ...     {"role": "reply", "kind": "text", "person": "marge", "time": "unknown",
        ...      "quote": "So warm!"}]}
        >>> [(s.kind, len(s.media), s.words) for s in sections_of(card)]
        [('images', 2, 'At the lake.'), ('file', 1, ''), ('added', 0, 'So warm!')]

    Photos in a row with no words after the first form one section of at
    most `MOSAIC_MAX` photos; one photo alone is an Image section. Several
    recordings in a row stay one Voice section with their words joined.
    """
    if card.get("type") not in SOURCE_TYPES:
        return []
    sections: list[Section] = []
    for source in sources_of_card(card):
        role = source.get("role")
        if role == "note":
            continue
        medium, said = medium_and_words(source)
        if medium is None and not said:
            continue
        if role == "reply":
            sections.append(Section("added", (medium,) if medium else (), said,
                                    _seconds(source), by=str(source.get("person") or ""),
                                    at=_known(source.get("time"))))
            continue
        kind = medium.kind if medium else _kind(source)
        if sections and _joins(sections[-1], kind, medium, said):
            sections[-1] = _joined(sections[-1], medium, said, _seconds(source))
            continue
        sections.append(_section(kind, medium, said, _seconds(source)))
    return sections


def media_paths(card: Mapping) -> list[str]:
    """The media store's paths of a card's files, in the order they
    were sent, replies' included, for the page to read their records. []
    for any other card.

        >>> media_paths({"type": "diary", "sources": [
        ...     {"role": "original", "kind": "voice", "quote": "![[/media/2026/11/v.m4a]]"}]})
        ['2026/11/v.m4a']
    """
    if card.get("type") not in SOURCE_TYPES:
        return []
    return [m.path for s in sections_of(card) for m in s.media]


def date_basis(card: Mapping) -> str:
    """How the entry's day was found (`spoken`, `sent`, `uncertain`,
    `corrected`), kept on its first source; "" when the card does not say.

        >>> date_basis({"sources": [{"role": "original", "date_basis": "spoken"}]})
        'spoken'
    """
    first = next((s for s in sources_of_card(card) if s.get("role") == "original"), {})
    return str(first.get("date_basis") or "")



# ── Reading the sources ────────────────────────────────────────────────────

# A mosaic holds 2 to 10 photos, the most a page shows at thumbnail size;
# the 11th photo in a row starts the next section.
MOSAIC_MAX = 10

_KINDS = {**dict.fromkeys(("m4a", "mp3", "wav", "ogg", "oga", "opus", "aac"), "voice"),
          **dict.fromkeys(("png", "jpg", "jpeg", "gif", "webp", "heic"), "image"),
          **dict.fromkeys(("mp4", "mov", "webm"), "video")}
_MEDIUMS = ("voice", "image", "video", "file")
# A quote's first line, when it is the file's embed or link
# (`stack.cards.sources.Source.media_link`).
_MEDIA_LINE = re.compile(r"^(?:!\[\[(/media/[^\]]+)\]\]|\[[^\]]*\]\((/media/[^)\s]+)\))$")
# The characters a path inside the media store has; anything else is not one.
_SAFE = re.compile(r"[\w./-]+")


def sources_of_card(card: Mapping) -> list[Mapping]:
    """The card's sources that are records (the cards API's `sources`), in order sent; [] for none."""
    return [s for s in card.get("sources") or [] if isinstance(s, Mapping)]


def medium_and_words(source: Mapping) -> tuple[Medium | None, str]:
    """A source's file (None for none, or a path that leaves the store) and
    its words: the quote after the embed. A card's history reads a source
    the same way (`history_lines`)."""
    lines = str(source.get("quote") or "").strip().split("\n")
    found = _MEDIA_LINE.match(lines[0].strip()) if lines else None
    if found is None:
        return None, "\n".join(lines).strip()
    said = "\n".join(lines[1:]).strip()
    path = (found[1] or found[2]).removeprefix("/media/")
    if not _SAFE.fullmatch(path) or ".." in path.split("/"):
        return None, said
    kind = str(source.get("kind") or "")
    if kind not in _MEDIUMS:
        kind = _KINDS.get(path.rsplit(".", 1)[-1].lower(), "file")
    return Medium(path, kind), said


def _kind(source: Mapping) -> str:
    """The section a source without a file opens: a recording or a photo
    whose file was not kept still shows what it was, words under it."""
    kind = str(source.get("kind") or "text")
    return kind if kind in SECTION_KINDS else "text"


def _seconds(source: Mapping) -> int:
    value = source.get("duration_ms")
    return int(value) // 1000 if isinstance(value, (int, float)) and value > 0 else 0


def _known(time: object) -> str:
    value = str(time or "")
    return "" if value in ("", "unknown") else value


def _joins(last: Section, kind: str, medium: Medium | None, said: str) -> bool:
    """Whether a source continues the section before it: a photo without
    words after photos (a gallery, up to `MOSAIC_MAX`), or a recording
    after a recording (one recording cut in two)."""
    if kind == "image" and medium is not None and not said:
        return last.kind in ("image", "images") and 0 < len(last.media) < MOSAIC_MAX
    return kind == "voice" and last.kind == "voice"


def _joined(last: Section, medium: Medium | None, said: str, seconds: int) -> Section:
    media = last.media + ((medium,) if medium else ())
    kind = "images" if last.kind in ("image", "images") and len(media) > 1 else last.kind
    words_ = " ".join(w for w in (last.words, said) if w)
    return replace(last, kind=kind, media=media, words=words_, seconds=last.seconds + seconds)


def _section(kind: str, medium: Medium | None, said: str, seconds: int) -> Section:
    if kind not in ("voice", "video"):
        seconds = 0
    return Section(kind if kind in SECTION_KINDS else "text",
                   (medium,) if medium else (), said, seconds)

