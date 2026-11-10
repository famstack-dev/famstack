"""A card's page: one layout for every card type, read only.

`/card/<card id>` shows one card of the family memory as a paper sheet
in the wiki's look, from the top:

    ┌──────────────────────────────────────────────────┐
    │ ▢ DOCUMENT                              today    │  kicker, created
    │ Duff Insurance policy                            │  title
    │ Duff Insurance · 2 October · Policy              │  facts line
    │ Insurance                                        │  the type's extra
    │ ┃ OPEN IN PAPERLESS  Paperless  …/72/details ↗   │  outside link
    │ ▣ photo, Open the image ↗  · "caption"           │  a note from a photo
    │ The policy covers the car.                       │  the text
    │ ▶ 1:12  "Grandma is here! ..."                   │  a diary entry's sections
    │ › FULL TEXT                                      │  a note from a file
    │ PEOPLE  (Homer)     TAGS  (#medical) (#car-ins.) │  chips
    │ Read it in the wiki ↗                            │  when compiled
    │ › HISTORY                                        │  closed, lazy
    │ › DETAILS                                        │  closed
    └──────────────────────────────────────────────────┘

    html = page(card, names={"homer": "Homer"}, now=now, language="en")

A diary entry (`entry.py`) shows famstack's summary as its text, then
its sections in the order they were sent: a voice message with its
transcript, a photo, 2 to 10 photos as a mosaic, a video, a file, and
each reply under who added it.

A note made from a file (`file_notes.py`) shows its summary as a
bookmark does: the panel opens the file from the media store, and its
full text waits behind "Full text", closed, which the page's script asks
for when it is opened (`addresses.text_address`): a PDF's text can run to many
pages, and most visits read only the summary. A note made from a photo
shows the photo in a bordered panel with "Open the image", then the
sender's caption, quoted, and famstack's description, which is the card's
text.

What changes per type (facts, extra, outside link, icon) is in
`card_types.py`; the text is the type's renderer's (`stack.card_render`,
ADR-016 §4), the same the chat shows, made safe HTML by `text.py`.

What it deliberately does not show: links between cards ("linked from",
"links to", a graph), and anything to edit a card, on any device. Editing
comes later, in place on the card itself; the sheet's top right corner
keeps room for it next to the creation date.

Pure: no I/O. `card` is the cards API's answer to `GET /card/<id>`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from html import escape

from memory_ui.card_types import Head, Outside, Part, head_of
from memory_ui.entry import draw, narrative, sections_of
from memory_ui.addresses import history_address, media_address, text_address
from memory_ui.file_notes import FileNote, file_of
from memory_ui.frame import document, icon
from memory_ui.lists import body as list_body
from memory_ui.preview import description
from memory_ui.text import to_html
from memory_ui.words import created, words
from stack.card_render import render
from stack.cards import ARCHIVE
from stack.cards.sources import SOURCES_HEADING

__all__ = ["page", "notice", "full_text"]


def page(card: Mapping, *, names: Mapping[str, str], now: datetime, language: str,
         kept: Mapping | None = None, paperless: str = "",
         media: Mapping[str, Mapping | None] | None = None) -> str:
    """The whole page of `card`. `names` maps a person id to the name a
    reader knows; `now` is aware, in the instance's time zone; `kept` is
    the media store's record of the file a note was made from, None when
    the store has none (then the page shows no panel for it); `paperless`
    is Paperless's public address today, for a document's "Open in
    Paperless" (`card_types`); `media` is the store's record of each file
    of a diary entry, by its path (`entry.media_paths`).

        page({"id": "note-0b6e2d91aa", "type": "note", "title": "Grill",
              "path": "homer/notes/grill.md", "meta": {}, "text": "Buy charcoal."},
             names={}, now=now, language="en")
        # an HTML document whose sheet says "Note", "Grill", "Buy charcoal."
    """
    w = words(language)
    head = head_of(card, names=names, today=now.date(), language=language, kept=kept,
                   paperless=paperless)
    found = file_of(card)
    title = str(card.get("title") or card.get("id") or "")
    body = _rendered(card, names, language, summary_only=found is not None)
    sections = sections_of(card)
    sheet = "".join([
        f'<article class="sheet card" data-type="{escape(str(card.get("type") or ""))}">',
        f'<div class="cardtop">{_created(card, now, language)}</div>',
        _head(head, title),
        _outside(head.outside),
        _photo(found, kept, title, w),
        (f'<div class="body">{_body(card, body, names, now, language)}</div>'
         if body.strip() or not sections else ""),
        draw(sections, media=media or {}, names=names, language=language),
        _full_text(card, found, w),
        _chips(w["people"], [Part(_name(p, names)) for p in _strings(card.get("persons"))]),
        _chips(w["tags"], _tags(card)),
        _wiki(card, w),
        _history(card, w),
        _details(card, found, w),
        "</article>"])
    # A chat app previews a link to the card by this description.
    facts = " · ".join(p.text for p in head.facts)
    return document(sheet, title=title, language=language,
                    description=description(body) or facts)


def notice(text: str, *, language: str, status_title: str = "") -> str:
    """A page that says one thing instead of a card: no card has the
    address, or the memory does not answer.

        notice(words("en")["missing"], language="en")
    """
    sheet = f'<article class="sheet card"><p class="notice">{escape(text)}</p></article>'
    return document(sheet, title=status_title or text, language=language)


def full_text(card: Mapping, *, language: str) -> str | None:
    """The full text of a note made from a file, as the fragment "Full
    text" puts in its place: the file's words its source quotes, as safe
    HTML. None for a card that is no note made from a file, or a photo.

        full_text(uno_rules, language="en")
        # '<p>Each player gets seven cards. ...</p>'
    """
    found = file_of(card)
    if found is None or found.is_image:
        return None
    if not found.text:
        return f'<p class="empty-text">{escape(words(language)["nothing"])}</p>'
    return to_html(found.text, here=str(card.get("path") or ""))


# ── The head ───────────────────────────────────────────────────────────────

def _created(card: Mapping, now: datetime, language: str) -> str:
    """When the card was made, in plain words, from its `timestamp`; nothing
    for a card that has none (a person, a list)."""
    stamp = str((card.get("meta") or {}).get("timestamp") or "")
    try:
        at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return ""
    plain, full = created(at, now=now, language=language)
    return (f'<time class="created" datetime="{escape(stamp)}" title="{escape(full)}" '
            f'aria-label="{escape(full)}">{escape(plain)}</time>')


def _head(head: Head, title: str) -> str:
    extra = f'<p class="extra">{_parts(head.extra)}</p>' if head.extra else ""
    facts = f'<p class="facts">{_parts(head.facts)}</p>' if head.facts else ""
    return (f'<div class="family-head"><p class="kicker">{icon(head.icon)}{escape(head.label)}</p>'
            f'<h1 class="title">{escape(title)}</h1>{facts}{extra}</div>')


def _parts(parts: list[Part]) -> str:
    def one(p: Part) -> str:
        clip = icon("clip", 12) if p.clip else ""
        text = escape(p.text)
        return clip + (f'<a class="internal quiet" href="{escape(p.href)}">{text}</a>' if p.href else text)
    return " · ".join(one(p) for p in parts)


def _outside(o: Outside | None) -> str:
    """The outside link as the wiki's bookmark panel: the order, the site,
    the address."""
    if o is None:
        return ""
    return (f'<a class="target" href="{escape(o.href)}" target="_blank" rel="noopener noreferrer">'
            f'<span class="bt-label">{escape(o.label)}</span>'
            f'<span class="bt-site">{escape(o.site)}</span>'
            f'<span class="bt-address">{escape(o.address)} <span class="bt-arrow" aria-hidden="true">↗</span>'
            '</span></a>')


# ── A note made from a file ────────────────────────────────────────────────

def _photo(found: FileNote | None, kept: Mapping | None, title: str, w: dict) -> str:
    """A photo in the bordered panel, a link to it at full size, and the
    sender's caption under it, quoted. Without the media store's record
    only the caption shows: no broken image."""
    if found is None or not found.is_image:
        return ""
    caption = (f'<blockquote class="caption">{to_html(found.text)}</blockquote>'
               if found.text else "")
    if not kept:
        return caption
    src = escape(media_address(found.path))
    return (f'<figure class="photo"><img src="{src}" alt="{escape(title)}" loading="lazy">'
            f'<figcaption><a class="external" href="{src}" target="_blank" rel="noopener">'
            f'{escape(w["open"]["image"])} <span class="bt-arrow" aria-hidden="true">↗</span></a>'
            f'</figcaption></figure>{caption}')


def _full_text(card: Mapping, found: FileNote | None, w: dict) -> str:
    """"Full text", closed. It carries no text: the page's script asks for
    it when it is opened (`addresses.text_address`)."""
    if found is None or found.is_image:
        return ""
    src = text_address(str(card.get("id") or ""))
    return (f'<details class="more fulltext" data-src="{escape(src)}">'
            f'<summary><span class="label">{escape(w["full_text"])}</span></summary>'
            f'<p class="loading" role="status">{escape(w["loading_text"])}</p></details>')


# ── The text, people, tags and the wiki ────────────────────────────────────

def _rendered(card: Mapping, names: Mapping[str, str], language: str, *,
              summary_only: bool = False) -> str:
    """The card's text as its type's renderer writes it (markdown), below
    the title and the facts line, which the head shows already. A diary
    entry's text is its narrative only: its sections show the rest.

    With `summary_only` (a note made from a file) the text stops before
    its sources: they quote the whole file, which "Full text" loads, and
    a photo's caption, which shows above. The link preview's description
    then reads the summary too, not the file."""
    fields = {**(card.get("meta") or {}), "type": card.get("type"), "title": card.get("title")}
    text = str(card.get("text") or "")
    if summary_only:
        text = text.split(f"\n{SOURCES_HEADING}\n", 1)[0]
    if card.get("type") == "diary":
        text = narrative(text)
    return render(fields, text, names=names, language=language).body


def _body(card: Mapping, body: str, names: Mapping[str, str], now: datetime,
          language: str) -> str:
    """The rendered text as safe HTML, or a line that nothing is written
    yet. A list shows its sections and items instead (`lists.py`): its
    rendered text is the file's checkbox lines, with every ticked item."""
    if card.get("type") in _LISTS:
        return list_body(card, names=names, today=now.date(), language=language)
    if not body.strip():
        return f'<p class="empty-text">{escape(words(language)["nothing"])}</p>'
    return to_html(body, here=str(card.get("path") or ""))


# A list and a month of its archive (the graph's card type for it).
_LISTS = ("list", ARCHIVE)


def _chips(label: str, parts: list[Part]) -> str:
    if not parts:
        return ""
    chips = "".join(f'<a class="chip" href="{escape(p.href)}">{escape(p.text)}</a>' if p.href
                    else f'<span class="chip">{escape(p.text)}</span>' for p in parts)
    return f'<div class="field"><p class="label">{escape(label)}</p><div class="chiprow">{chips}</div></div>'


def _tags(card: Mapping) -> list[Part]:
    """The card's tags as their labels (a topic's id shows as its name),
    as plain chips: the card pages have no search to lead to. `Person:` tags repeat
    `persons` and are left out (ADR-017 §4)."""
    labels = card.get("tag_labels") or {}
    tags = _strings((card.get("meta") or {}).get("tags"))
    shown = [str(labels.get(t, t)) for t in tags if not t.lower().startswith("person:")]
    return [Part(f"#{t}") for t in shown]


def _wiki(card: Mapping, w: dict) -> str:
    """"Read it in the wiki", when the wiki compiled a page from this card."""
    go = next((str(v.get("go")) for v in card.get("views") or [] if v.get("go")), "")
    if not go:
        return ""
    return (f'<p class="wiki"><a class="external" href="{escape(go)}" target="_blank" rel="noopener">'
            f'{escape(w["wiki"])}<span class="ext" aria-hidden="true">↗</span></a></p>')


# ── History and details, closed ────────────────────────────────────────────

def _history(card: Mapping, w: dict) -> str:
    """History, closed. It carries no versions: the page's script asks for
    them when it is opened (`addresses.history_address`)."""
    src = history_address(str(card.get("id") or ""))
    return (f'<details class="more history" data-src="{escape(src)}">'
            f'<summary><span class="label">{escape(w["history"])}</span></summary>'
            f'<p class="loading" role="status">{escape(w["loading"])}</p></details>')


def _details(card: Mapping, found: FileNote | None, w: dict) -> str:
    rows = (f'<dt>{escape(w["card_id"])}</dt><dd><code>{escape(str(card.get("id") or ""))}</code></dd>'
            f'<dt>{escape(w["lives"])}</dt><dd><code>{escape(str(card.get("path") or ""))}</code></dd>')
    listed = [s for s in card.get("sources") or [] if isinstance(s, Mapping)]
    # A file's source is named by the file: its words wait behind "Full
    # text", and a photo and its caption show above.
    sources = "".join(_source(s, said=found.name if found and i == 0 else None)
                      for i, s in enumerate(listed))
    sources_html = (f'<h3 class="label subhead">{escape(w["sources"])}</h3>{sources}' if sources else "")
    return (f'<details class="more details"><summary><span class="label">{escape(w["details"])}</span>'
            f'</summary><dl class="facts-grid">{rows}</dl>{sources_html}</details>')


def _source(source: Mapping, *, said: str | None = None) -> str:
    """One source as the card's sidecar shows it: who and when, then its
    quote, or `said` in its place."""
    said = str(source.get("quote") or "") if said is None else said
    by = " · ".join(str(source.get(k)) for k in ("person", "time") if source.get(k))
    return (f'<p class="source-sender">{escape(by)}</p>' if by else "") + \
        (f"<blockquote>{to_html(said)}</blockquote>" if said else "")


# ── Small readers ──────────────────────────────────────────────────────────

def _name(person: str, names: Mapping[str, str]) -> str:
    return names.get(person.lower()) or (person[:1].upper() + person[1:])


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(v) for v in value or [] if str(v).strip()] if isinstance(value, list) else []
