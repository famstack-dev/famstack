"""Drawing a card's sections as the page's HTML.

    draw(sections_of(card), media=records, names={"homer": "Homer"},
         language="en", now=now)              # '<div class="parts">...</div>'

Each section gets its small icon in the left column and, beside it, what
it shows: a quoted text, a player with its transcript, a photo with its
caption, photos as a mosaic with a viewer the page's script opens, a
video's first frame, a file with "Open", or a reply under "Added by
Marge, 11 November". A medium the media store does not have shows what it
was, with no broken image or player: a copy of the memory without its
files links nothing that is not there.

The sections come from `sections.py`; `media` is the media store's
record of each file by path (`Reads.kept`). Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from html import escape

from memory_ui.addresses import media_address
from memory_ui.entry.sections import Medium, Section
from memory_ui.file_notes import file_kind, kind_label
from memory_ui.frame import icon
from memory_ui.text import to_html
from memory_ui.words import long_day, words

__all__ = ["draw"]


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
