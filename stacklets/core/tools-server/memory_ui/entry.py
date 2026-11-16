"""A diary entry's sections: what it holds, in the order it was sent.

A diary entry is a diary card (`type: diary`). It can gather several
messages: a voice message and the photos sent with it, a recording cut in
two, a reply in its thread. Its page shows it as a short story of
sections, each with its small icon, under famstack's title and summary:

    sections = sections_of(card)
    [s.kind for s in sections]            # ['voice', 'images', 'added']
    holds(sections, language="en")        # 'Voice and 7 photos'
    narrative(card["text"])               # the summary, without the family's words
    draw(sections, media=records, names={"homer": "Homer"}, language="en", now=now)
                                          # '<div class="parts">...</div>'

| Section | Shows |
|---|---|
| text | the words, quoted |
| voice | a player "▶ 0:41", the transcript under it, quoted |
| image | the photo, its caption under it, quoted |
| images | 2 to 10 photos as a mosaic by count; a photo opens full size with "3 of 7" |
| video | the first frame with "▶ 0:12" |
| file | its name, its kind ("PDF"), "Open" |
| added | "Added by Marge, 11 November" above a reply's file and words, quoted |

The sections are read from the card's sources (ADR-018), which the cards
API gives with each card (`card["sources"]`): one per message and per
reply, in the order sent, each with who sent it, when, its kind, its
length, and its quote, the file's embed and then the words that came with
it. The card's text is never parsed for them.

Worked example: the card "At the lake" has five sources: three photos
of Homer's, each with its caption, a line of his, and a reply of
Marge's the next day. Its sections are three Image sections, each photo
with its own caption, a Text section, and an Added section ("Added by
Marge, 11 November"). Photos sent in a row with no words of their own
(a gallery) form one mosaic, under the first photo's caption. Recordings
in a row (one recording cut in two) are one Voice section with their
words joined. A `note` source (a correction given to the model) is no
part of the entry and shows nowhere here. A reply whose time a migrated
card could not record (`unknown`) says who added it, not when.

Quotes are the family's words only. The title and the summary are
famstack's narrative: `narrative` keeps them plain, and the card's
"Quotes" (excerpts of the words, which the sections show whole) and its
"Sources" are left out.

A medium the media store does not have (`media[path]` is None) shows what
it was, with no broken image or player: a copy of the memory without its
files links nothing that is not there.

Pure: no I/O. `card` is the cards API's answer to `GET /card/<id>`;
`media` is the media store's record of each file (`Reads.kept`), by path.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from html import escape

from memory_ui.addresses import media_address
from memory_ui.file_notes import file_kind, kind_label
from memory_ui.frame import icon
from memory_ui.text import to_html
from memory_ui.words import long_day, words

__all__ = ["Medium", "Section", "sections_of", "media_paths", "date_basis", "holds",
           "narrative", "draw"]


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
    """The sections of a diary card, in the order its messages were sent,
    from its sources. [] for a card that is no diary entry, or has no sources.

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
    if card.get("type") != "diary":
        return []
    sections: list[Section] = []
    for source in _sources(card):
        role = source.get("role")
        if role == "note":
            continue
        medium, said = _medium_and_words(source)
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
    """The media store's paths of a diary card's files, in the order they
    were sent, replies' included, for the page to read their records. []
    for any other card.

        >>> media_paths({"type": "diary", "sources": [
        ...     {"role": "original", "kind": "voice", "quote": "![[/media/2026/11/v.m4a]]"}]})
        ['2026/11/v.m4a']
    """
    if card.get("type") != "diary":
        return []
    return [m.path for s in sections_of(card) for m in s.media]


def date_basis(card: Mapping) -> str:
    """How the entry's day was found (`spoken`, `sent`, `uncertain`,
    `corrected`), kept on its first source; "" when the card does not say.

        >>> date_basis({"sources": [{"role": "original", "date_basis": "spoken"}]})
        'spoken'
    """
    first = next((s for s in _sources(card) if s.get("role") == "original"), {})
    return str(first.get("date_basis") or "")


def holds(sections: list[Section], *, language: str) -> str:
    """What an entry holds, for its facts line: each kind once, with its
    count, in the order the sections show them.

        >>> s = [Section("voice"), Section("images", (Medium("a.png", "image"),) * 7)]
        >>> holds(s, language="en"), holds(s, language="de")
        ('Voice and 7 photos', 'Sprachnachricht und 7 Fotos')
        >>> holds([Section("video"), Section("file"), Section("added")], language="en")
        'Video, a file and a reply'
        >>> holds([], language="en")
        ''
    """
    w = words(language)
    counts: dict[str, int] = {}
    for section in sections:
        what = _COUNTED.get(section.kind, section.kind)
        counts[what] = counts.get(what, 0) + _count(section)
    items = [_holding(what, n, w, first=i == 0) for i, (what, n) in enumerate(counts.items())]
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} {w['and']} {items[-1]}"


def narrative(text: str) -> str:
    """The card's text without what the sections show: its quotes and its
    sources. What is left is famstack's narrative (the meta line, the
    summary) and any section a person added.

        >>> narrative("> [!summary]\\n> A day at the lake.\\n\\n## Sources\\n\\n"
        ...           "> ![[/media/2026/08/a.png]]\\n> At the lake.\\n")
        '> [!summary]\\n> A day at the lake.'
    """
    kept: list[str] = []
    for heading, lines in _split(text):
        if heading in _SHOWN_AS_SECTIONS:
            continue
        if heading:
            kept.append(f"## {heading}")
        kept += lines
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def draw(sections: list[Section], *, media: Mapping[str, Mapping | None],
         names: Mapping[str, str], language: str, now: datetime | None = None) -> str:
    """The sections as the page's HTML, "" for none. `media` is the media
    store's record of each file by its path (None or missing: the store
    does not have it); `names` maps a person id to the name a reader knows;
    `now` is aware, in the instance's time zone, and gives a reply's day
    ("Added by Marge, 11 November"); without it a reply names who added it.

        draw([Section("text", words="I played it without a mistake!")], media={},
             names={}, language="en")
        # '<div class="parts"><section class="part" data-section="text">...</section></div>'
    """
    if not sections:
        return ""
    view = _View(media, names, words(language), now, language)
    return '<div class="parts">' + "".join(view.section(s) for s in sections) + "</div>"


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
# The card's own sections that the entry's sections show, left out of the narrative.
_SHOWN_AS_SECTIONS = {"Quotes", "Sources"}
_HEADING = re.compile(r"^##\s+(.+?)\s*$")


def _sources(card: Mapping) -> list[Mapping]:
    return [s for s in card.get("sources") or [] if isinstance(s, Mapping)]


def _medium_and_words(source: Mapping) -> tuple[Medium | None, str]:
    """A source's file (None for none, or a path that leaves the store) and
    its words: the quote after the embed."""
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
    return kind if kind in _SECTION_ICONS else "text"


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
    return Section(kind if kind in _SECTION_ICONS else "text",
                   (medium,) if medium else (), said, seconds)


def _split(text: str) -> list[tuple[str, list[str]]]:
    """The text as (heading, lines) by its `## ` headings; the part before
    the first heading has the heading ""."""
    parts: list[tuple[str, list[str]]] = [("", [])]
    for line in text.split("\n"):
        if found := _HEADING.match(line):
            parts.append((found[1], []))
        else:
            parts[-1][1].append(line)
    return parts


# ── What an entry holds ────────────────────────────────────────────────────

# A section counts under the word the facts line uses for it.
_COUNTED = {"image": "photo", "images": "photo", "added": "reply"}


def _count(section: Section) -> int:
    """How many of its kind a section adds: a photo section its photos (one
    when the card names no file), any other section one."""
    return max(len(section.media), 1) if section.kind in ("image", "images") else 1


def _holding(what: str, n: int, w: dict, *, first: bool) -> str:
    """One item of the facts line: "Voice" or "7 photos" first, "a file"
    after it."""
    one, many = w["holds"][what]
    if n > 1:
        text = many.format(n=n)
    elif first:
        text = w["medium"].get("image" if what == "photo" else what) or one
    else:
        text = one
    return text[:1].upper() + text[1:] if first else text


# ── Drawing ────────────────────────────────────────────────────────────────

# The small icon of each kind of section (the mock's `PART_ICON`).
_SECTION_ICONS = {"text": "diary-text", "voice": "diary-voice", "image": "diary-photo",
                  "images": "photos", "video": "diary-video", "file": "document"}


@dataclass(frozen=True)
class _View:
    media: Mapping[str, Mapping | None]
    names: Mapping[str, str]
    w: dict
    now: datetime | None = None
    language: str = "en"

    def section(self, s: Section) -> str:
        """One section: its icon in the left column, then what it shows. A
        reply is its line "Added by Marge, 11 November" above its file and
        its words."""
        if s.kind == "added":
            return self._added(s)
        body = getattr(self, f"_{s.kind}")(s)
        return self._part(_SECTION_ICONS[s.kind], body, data=s.kind)

    def _added(self, s: Section) -> str:
        who = self.names.get(s.by.lower()) or (s.by[:1].upper() + s.by[1:])
        day = self._day(s.at)
        label = escape(self.w["added_by_on"].format(name=who, day=day) if day
                       else self.w["added_by"].format(name=who))
        if s.media:
            medium = s.media[0]
            shown = Section(medium.kind if medium.kind in _SECTION_ICONS else "file",
                            s.media, s.words, s.seconds)
            inner = self._part(_SECTION_ICONS[shown.kind], getattr(self, f"_{shown.kind}")(shown))
        else:
            inner = self._part("diary-text", self._words(s.words))
        return (f'<section class="part added" data-section="added">'
                f'<p class="added-label"><span class="icon-cell">{icon("reply", 14)}</span>'
                f'<span class="added-by">{label}</span></p>{inner}</section>')

    def _day(self, at: str) -> str:
        """A reply's day in the instance's time zone, "" when unknown."""
        if not at or self.now is None:
            return ""
        try:
            moment = datetime.fromisoformat(at.replace("Z", "+00:00"))
        except ValueError:
            return ""
        if moment.tzinfo is not None and self.now.tzinfo is not None:
            moment = moment.astimezone(self.now.tzinfo)
        return long_day(moment.date(), today=self.now.date(), language=self.language)


    def _part(self, name: str, body: str, *, data: str = "") -> str:
        section = f' data-section="{data}"' if data else ""
        tag = "section" if data else "div"
        return (f'<{tag} class="part"{section}><span class="icon-cell">{icon(name, 15)}</span>'
                f'<div class="part-body">{body}</div></{tag}>')

    # ── Each kind ──

    def _text(self, s: Section) -> str:
        return self._words(s.words)

    def _voice(self, s: Section) -> str:
        """A player for each recording ("▶ 1:12" when the card gives the
        length of one), then the transcript."""
        length = _length(s.seconds) if len(s.media) == 1 else ""
        players = "".join(self._player(m, length) for m in s.media)
        return players + self._words(s.words)

    def _player(self, m: Medium, length: str) -> str:
        if not self._kept(m):
            return self._missing(m)
        label = f"▶ {length}" if length else "▶"
        spoken = f'{self.w["open"]["play"]}, {length}' if length else self.w["open"]["play"]
        return (f'<div class="player"><button class="play playbtn" type="button" '
                f'aria-label="{escape(spoken)}">{label}</button>'
                f'<span class="wave" aria-hidden="true"></span>'
                f'<audio preload="none" src="{escape(media_address(m.path))}"></audio></div>')

    def _image(self, s: Section) -> str:
        """The photo, a link to it at full size, then its caption."""
        shown = "".join(self._photo(m) for m in s.media) if s.media else self._missing(None)
        return shown + self._words(s.words, caption=True)

    def _photo(self, m: Medium) -> str:
        if not self._kept(m):
            return self._missing(m)
        src = escape(media_address(m.path))
        return (f'<figure class="photo-fig"><a href="{src}" target="_blank" rel="noopener">'
                f'<img src="{src}" alt="" loading="lazy"></a></figure>')

    def _images(self, s: Section) -> str:
        """The mosaic, then the viewer the page's script opens over it (one
        photo full size, the arrows, "3 of 7", "All photos"), then the
        caption. Without the script each tile links its photo."""
        count = len(s.media)
        tiles = "".join(self._tile(m, n, count) for n, m in enumerate(s.media, 1))
        nav = (f'<button class="nav next" type="button" '
               f'aria-label="{escape(self.w["next_photo"])}">›</button>')
        viewer = (f'<div class="viewer" hidden>'
                  f'<button class="allphotos" type="button">{icon("grid", 14)}'
                  f'{escape(self.w["all_photos"])}</button>'
                  f'<figure class="photo-fig"><div class="frame swipe">'
                  f'<img class="shown" alt=""><p class="gone" hidden>{icon("diary-photo", 15)}'
                  f'{escape(self.w["not_kept"])}</p>'
                  f'<button class="nav prev" type="button" '
                  f'aria-label="{escape(self.w["previous_photo"])}">‹</button>{nav}</div>'
                  f'<figcaption class="slide-caption"><span class="count"></span></figcaption>'
                  f'</figure></div>')
        of = escape(self.w["of"].format(n="{n}", count=count))
        return (f'<div class="slides" data-of="{of}"><div class="mosaic-wrap">'
                f'<div class="mosaic mosaic-{count}">{tiles}</div>{nav}</div>{viewer}</div>'
                + self._words(s.words, caption=True))

    def _tile(self, m: Medium, n: int, count: int) -> str:
        spoken = f'{self.w["medium"]["image"]} {self.w["of"].format(n=n, count=count)}'
        if not self._kept(m):
            return (f'<span class="tile gone-tile" role="img" aria-label="{escape(spoken)}">'
                    f'{icon("diary-photo", 18)}<span class="missing">{escape(self.w["not_kept"])}'
                    f'</span></span>')
        src = escape(media_address(m.path))
        return (f'<a class="tile" href="{src}" aria-label="{escape(spoken)}">'
                f'<img src="{src}" alt="" loading="lazy"></a>')

    def _video(self, s: Section) -> str:
        """The first frame with "▶ 0:12" on it; the page's script plays it."""
        if not s.media or not self._kept(s.media[0]):
            return self._missing(s.media[0] if s.media else None) + self._words(s.words, caption=True)
        length = _length(s.seconds)
        label = f"▶ {length}" if length else "▶"
        spoken = f'{self.w["play_video"]}, {length}' if length else self.w["play_video"]
        # `#t=0.1` makes Safari draw the first frame before anything plays.
        src = escape(media_address(s.media[0].path))
        return (f'<figure class="photo-fig video"><div class="frame">'
                f'<video preload="metadata" playsinline src="{src}#t=0.1"></video>'
                f'<button class="playover" type="button" aria-label="{escape(spoken)}">{label}'
                f'</button></div></figure>' + self._words(s.words, caption=True))

    def _file(self, s: Section) -> str:
        """Its name as it was sent, its kind, and "Open"."""
        rows = []
        for m in s.media:
            record = self.media.get(m.path) or {}
            name = str(record.get("filename") or m.path.rsplit("/", 1)[-1])
            kind = kind_label(file_kind(m.path), self.w)
            opens = (f'<a class="inlinebtn" href="{escape(media_address(m.path))}" target="_blank" '
                     f'rel="noopener">{escape(self.w["open_file"])}</a>' if record
                     else f'<span class="missing">{escape(self.w["not_kept"])}</span>')
            rows.append(f'<div class="filerow"><span class="filename">{escape(name)}</span>'
                        f'<span class="filekind">{escape(kind)}</span>{opens}</div>')
        return "".join(rows) + self._words(s.words, caption=True)

    # ── Shared pieces ──

    def _kept(self, m: Medium) -> bool:
        return bool(self.media.get(m.path))

    def _missing(self, m: Medium | None) -> str:
        """What a medium was, in place of a file the store does not have."""
        what = self.w["medium"].get(m.kind if m else "image", "")
        return (f'<p class="missing-row">{escape(what)} · '
                f'<span class="missing">{escape(self.w["not_kept"])}</span></p>')

    @staticmethod
    def _words(said: str, *, caption: bool = False) -> str:
        """The family's words, quoted; "" for none."""
        if not said.strip():
            return ""
        kind = "words caption" if caption else "words"
        return f'<blockquote class="{kind}">{to_html(said)}</blockquote>'


def _length(seconds: int) -> str:
    """A length as a player shows it: "1:12"; "" for none.

        >>> _length(72), _length(0)
        ('1:12', '')
    """
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds > 0 else ""
