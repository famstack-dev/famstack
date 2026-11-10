"""A diary entry's sections: what it holds, in the order it was sent.

A diary entry is a diary card (`type: diary`). It can gather several
messages: a voice message and the photos sent with it, a recording cut in
two, a reply in its thread. Its page shows it as a short story of
sections, each with its small icon, under famstack's title and summary:

    sections = sections_of(card)
    [s.kind for s in sections]            # ['voice', 'images', 'added']
    holds(sections, language="en")        # 'Voice and 7 photos'
    narrative(card["text"])               # the summary, without the family's words
    draw(sections, media=records, names={"homer": "Homer"}, language="en")
                                          # '<div class="parts">...</div>'

| Section | Shows |
|---|---|
| text | the words, quoted |
| voice | a player "▶ 0:41", the transcript under it, quoted |
| image | the photo, its caption under it, quoted |
| images | 2 to 10 photos as a mosaic by count; a photo opens full size with "3 of 7" |
| video | the first frame with "▶ 0:12" |
| file | its name, its kind ("PDF"), "Open" |
| added | "Added by Marge" above a reply's words, quoted |

Worked example: the card "Grandma visited" holds `kind: voice`,
`media: [voice.m4a, 7 photos]`, `duration_ms: 72000`, the transcript under
`## Transcript` and one reply of Homer's under `## Replies`. Its sections
are a Voice section (the recording, "▶ 1:12", the transcript), an Images
section (the seven photos) and an Added section ("Added by Homer").

Why the sections are read this way: the card stores its media in the
order they were sent, one block of words (the first message's transcript,
text or caption), and its replies as who wrote them and what. So the
first message's medium gets the words, the other media follow in order,
and the replies come last. The card does not store a caption for each
photo, a reply's day, or which reply carried which photo; the page shows
what the card holds and adds none of it (docs/memory-format.md, "diary").
So the mosaic has one caption under it, not one per photo, and a reply
says who added it, not when.

Quotes are the family's words only. The title and the summary are
famstack's narrative: `narrative` keeps them plain, and the card's
"Quotes" (excerpts of the words, which the sections show whole) are left
out.

A medium the media store does not have (`media[path]` is None) shows what
it was, with no broken image or player: a copy of the memory without its
files links nothing that is not there.

Pure: no I/O. `card` is the cards API's answer to `GET /card/<id>`;
`media` is the media store's record of each file (`Reads.kept`), by path.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape

from memory_ui.addresses import media_address
from memory_ui.file_notes import file_kind, kind_label
from memory_ui.frame import icon
from memory_ui.text import to_html
from memory_ui.words import words

__all__ = ["Medium", "Section", "sections_of", "media_paths", "holds", "narrative", "draw"]


@dataclass(frozen=True)
class Medium:
    """One file of an entry: where the media store keeps it, and its kind
    ("voice", "image", "video" or "file"), read from its extension.

        Medium("2026/11/GrandmaVoiceEvent.m4a", "voice")
    """

    path: str
    kind: str


@dataclass(frozen=True)
class Section:
    """One section of an entry: its kind (a row of the table above), its
    media, the family's words in it, a recording's or a video's length in
    seconds (0 when the card does not say), and for a reply who added it.

        Section("voice", (Medium("2026/11/v.m4a", "voice"),), "Grandma is here!", 72)
        Section("added", words="She forgot her umbrella again.", by="homer")
    """

    kind: str
    media: tuple[Medium, ...] = ()
    words: str = ""
    seconds: int = 0
    by: str = ""


def sections_of(card: Mapping) -> list[Section]:
    """The sections of a diary card, in the order they were sent: the first
    message's medium with the words, the other media, then the replies.
    [] for a card that is no diary entry.

        >>> card = {"type": "diary", "meta": {"kind": "image", "media": [
        ...     "/media/2026/08/a.png", "/media/2026/08/b.png", "/media/2026/08/c.pdf"]},
        ...     "text": "## Caption\\n\\nAt the lake."}
        >>> [(s.kind, len(s.media), s.words) for s in sections_of(card)]
        [('images', 2, 'At the lake.'), ('file', 1, '')]

    Photos in a row form one section of at most `MOSAIC_MAX` photos; one
    photo alone is an Image section. Several recordings in a row (one
    recording cut in two) stay one Voice section with one transcript.
    """
    if card.get("type") != "diary":
        return []
    meta = card.get("meta") or {}
    kind = str(meta.get("kind") or "text")
    text = str(card.get("text") or "")
    media = _media(meta.get("media"))
    first = _leading_run(media, kind)
    seconds = int(meta.get("duration_ms") or 0) // 1000
    head = _first_section(kind, first, _words(text), seconds)
    replies = [Section("added", words=said, by=who) for who, said in _replies(text)]
    return head + _grouped(media[len(first):]) + replies


def media_paths(card: Mapping) -> list[str]:
    """The media store's paths of a diary card's files, in the order they
    were sent, for the page to read their records. [] for any other card.

        >>> media_paths({"type": "diary", "meta": {"media": ["/media/2026/11/v.m4a"]}})
        ['2026/11/v.m4a']
    """
    if card.get("type") != "diary":
        return []
    return [m.path for m in _media((card.get("meta") or {}).get("media"))]


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
    """The card's text without what the sections show: the family's words,
    the replies, the quotes and the media. What is left is famstack's
    narrative (the meta line, the summary) and any section a person added.

        >>> narrative("> [!summary]\\n> A day at the lake.\\n\\n![[/media/2026/08/a.png]]\\n\\n"
        ...           "## Caption\\n\\nAt the lake.\\n")
        '> [!summary]\\n> A day at the lake.'
    """
    kept: list[str] = []
    for heading, lines in _split(text):
        if heading in _SHOWN_AS_SECTIONS:
            continue
        if heading:
            kept.append(f"## {heading}")
        kept += [ln for ln in lines if not _MEDIA_LINE.fullmatch(ln.strip())]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def draw(sections: list[Section], *, media: Mapping[str, Mapping | None],
         names: Mapping[str, str], language: str) -> str:
    """The sections as the page's HTML, "" for none. `media` is the media
    store's record of each file by its path (None or missing: the store
    does not have it); `names` maps a person id to the name a reader knows.

        draw([Section("text", words="I played it without a mistake!")], media={},
             names={}, language="en")
        # '<div class="parts"><section class="part" data-section="text">...</section></div>'
    """
    if not sections:
        return ""
    view = _View(media, names, words(language))
    return '<div class="parts">' + "".join(view.section(s) for s in sections) + "</div>"


# ── Reading the card ───────────────────────────────────────────────────────

# A mosaic holds 2 to 10 photos, the most a page shows at thumbnail size;
# the 11th photo in a row starts the next section.
MOSAIC_MAX = 10

_KINDS = {**dict.fromkeys(("m4a", "mp3", "wav", "ogg", "oga", "opus"), "voice"),
          **dict.fromkeys(("png", "jpg", "jpeg", "gif", "webp", "heic"), "image"),
          **dict.fromkeys(("mp4", "mov", "webm"), "video")}
# The headings the diary files the family's words, its quotes and its
# replies under (`diary_card._unsigned`).
_WORDS = {"Transcript", "Text", "Caption"}
_REPLIES, _QUOTES = "Replies", "Quotes"
_SHOWN_AS_SECTIONS = _WORDS | {_REPLIES, _QUOTES}
_HEADING = re.compile(r"^##\s+(.+?)\s*$")
_AUTHOR = re.compile(r"^\*\*(.+)\*\*$")
_MEDIA_LINE = re.compile(r"!\[\[/?media/[^\]]+\]\]|\[[^\]]+\]\(/?media/[^)\s]+\)")
# The characters a path inside the media store has; anything else is not one.
_SAFE = re.compile(r"[\w./-]+")


def _media(paths: object) -> list[Medium]:
    """The card's `media` (`/media/2026/11/x.png`) as the store's paths,
    each with its kind. A path that leaves the store is no medium."""
    found = []
    for raw in paths if isinstance(paths, list) else []:
        path = str(raw).strip().removeprefix("/").removeprefix("media/")
        if _SAFE.fullmatch(path) and ".." not in path.split("/"):
            found.append(Medium(path, _KINDS.get(path.rsplit(".", 1)[-1].lower(), "file")))
    return found


def _leading_run(media: list[Medium], kind: str) -> list[Medium]:
    """The first message's media: the leading files of the card's kind (a
    recording cut in two is two files), at most a mosaic of photos."""
    run: list[Medium] = []
    for medium in media:
        if medium.kind != kind or (kind == "image" and len(run) == MOSAIC_MAX):
            break
        run.append(medium)
    return run


def _first_section(kind: str, run: list[Medium], said: str, seconds: int) -> list[Section]:
    """The section of the first message, with the words under its medium.
    A medium the card names no file for still gets its section with the
    words, so a transcript is never lost; an entry with neither gets none."""
    if not run and not said:
        return []
    if kind == "image":
        kind = "images" if len(run) > 1 else "image"
    if kind not in ("voice", "video"):
        seconds = 0
    return [Section(kind if kind in _SECTION_ICONS else "text", tuple(run), said, seconds)]


def _grouped(media: list[Medium]) -> list[Section]:
    """The other media in order: photos in a row as one section of at most
    `MOSAIC_MAX`, each recording, video and file as its own."""
    sections: list[Section] = []
    photos: list[Medium] = []
    for medium in [*media, None]:
        if medium is not None and medium.kind == "image" and len(photos) < MOSAIC_MAX:
            photos.append(medium)
            continue
        if photos:
            sections.append(Section("images" if len(photos) > 1 else "image", tuple(photos)))
            photos = []
        if medium is None:
            continue
        if medium.kind == "image":
            photos.append(medium)
        else:
            sections.append(Section(medium.kind, (medium,)))
    return sections


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


def _words(text: str) -> str:
    """The family's words: the section the diary files them under."""
    for heading, lines in _split(text):
        if heading in _WORDS:
            return "\n".join(lines).strip()
    return ""


def _replies(text: str) -> list[tuple[str, str]]:
    """(who, words) of each reply: `**marge**` and the quoted lines under it."""
    found: list[tuple[str, list[str]]] = []
    for heading, lines in _split(text):
        if heading != _REPLIES:
            continue
        for line in lines:
            if author := _AUTHOR.match(line.strip()):
                found.append((author[1], []))
            elif found and line.startswith(">"):
                found[-1][1].append(line[1:].removeprefix(" "))
    return [(who, "\n".join(said).strip()) for who, said in found]


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

    def section(self, s: Section) -> str:
        """One section: its icon in the left column, then what it shows. A
        reply is its line "Added by Marge" above its words."""
        if s.kind == "added":
            who = self.names.get(s.by.lower()) or (s.by[:1].upper() + s.by[1:])
            label = escape(self.w["added_by"].format(name=who))
            return (f'<section class="part added" data-section="added">'
                    f'<p class="added-label"><span class="icon-cell">{icon("reply", 14)}</span>'
                    f'<span class="added-by">{label}</span></p>'
                    f'{self._part("diary-text", self._words(s.words))}</section>')
        body = getattr(self, f"_{s.kind}")(s)
        return self._part(_SECTION_ICONS[s.kind], body, data=s.kind)

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
