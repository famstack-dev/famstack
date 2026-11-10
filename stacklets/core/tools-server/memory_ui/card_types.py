"""What each card type shows at the top of its page: its icon and label,
its facts line, a quiet extra, and its link to the outside.

Every card page has one layout (`card_page.py`); the types differ only in
these four things, so they live in one table here, one function per type:

    head = head_of(card, names={"homer": "Homer"}, today=date(2026, 11, 10), language="en")
    head.label      # 'Document'
    head.facts      # [Part('Duff Insurance', '/card/family/correspondents/duff-insurance.md'),
                    #  Part('2 October'), Part('Policy')]
    head.extra      # [Part('Insurance')]
    head.outside    # Outside('Open in Paperless', <Paperless's page of the document>, 'Paperless', ...)

| Type | Facts line | Extra | Outside link |
|---|---|---|---|
| document | correspondent (its card), date, document type | category | "Open in Paperless" |
| bookmark | the page's host, saved by, date | | "Open the page" |
| note | who it is about, date | "in the chat" | |
| note from a file | who saved it, the room, date | | "Open the PDF" (its name, kind, size) |
| email | the sender, date | to whom, its attachments (their cards) | |
| diary | for whom, the day, what it holds ("Voice and 7 photos") | a date as said | |
| list | kind, topics | "3 open", a calendar's next date | |
| list-archive | "Archive", topics | | |
| person | other names, the chat account | | |
| correspondent | other names | phone | "Open the website" |
| topic | how many cards | when it last changed | |

Why the facts are worked out here and not taken from the card's
rendered facts line (`stack.card_render`): that line is markdown for the
chat ("2026-10-02 · Homer · Duff Insurance"), with ISO dates and no links.
A page says a date in plain words and links a correspondent's card.

Why a document's outside link is made from its `paperless_id` and not
taken from its `resource`: the card stores the Paperless address of the
day it was filed, which breaks when the hosting moves (port mode to domain
mode). So the page builds the link when it is shown, from the number and
Paperless's address today (`paperless`, core's `PAPERLESS_PUBLIC_URL`),
with `addresses.paperless_page`. Never `/go/docs/<n>`: that link
lands on this same page. Without Paperless's address there is no link.

A diary entry's media play in its sections (`entry.py`), so it has no
outside link.

A note made from a file (`file_notes.py`) says its file's kind in the
label ("Note · PDF") with a paper clip, and opens the file from the
media store; a note made from a photo shows the photo itself
(`card_page.py`), so it has no outside link. Without the media store's
record of the file (`kept`), the panel is left out: a copy of the memory
without its files links nothing that is not there.

Pure: no I/O. `card` is the cards API's answer to `GET /card/<id>`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlsplit

from stack.cards import ARCHIVE

from memory_ui.entry import holds, sections_of
from memory_ui.addresses import card_address, media_address, paperless_page
from memory_ui.file_notes import FileNote, file_of, kind_label, size_label
from memory_ui.lists import coming, open_count
from memory_ui.text import vault_path
from memory_ui.words import day_of, long_day, plain_day, words

__all__ = ["Part", "Outside", "Head", "head_of"]


@dataclass(frozen=True)
class Part:
    """One piece of a facts line or an extra: its text, and the address it
    links to ("" for plain text). `clip` marks an attachment.

        Part("Duff Insurance", "/card/family/correspondents/duff-insurance.md")
    """

    text: str
    href: str = ""
    clip: bool = False


@dataclass(frozen=True)
class Outside:
    """The card's link to the outside, drawn as the wiki's bookmark panel:
    what pressing it does (`label`), where it goes, the site's name and the
    address as a reader sees it.

        Outside("Open the page", "https://krusty.example/menu", "krusty.example",
                "krusty.example/menu")
    """

    label: str
    href: str
    site: str
    address: str


@dataclass(frozen=True)
class Head:
    """The top of a card's page, for one card: the type's `icon` (a key of
    the page's icons), its label, the facts line, the extra, the outside link."""

    icon: str
    label: str
    facts: list[Part] = field(default_factory=list)
    extra: list[Part] = field(default_factory=list)
    outside: Outside | None = None


def head_of(card: Mapping, *, names: Mapping[str, str], today: date, language: str,
            kept: Mapping | None = None, paperless: str = "") -> Head:
    """The head of `card`'s page. `names` maps a person id to the name a
    reader knows (`{"homer": "Homer"}`); `today` is the instance's day;
    `kept` is the media store's record of the file a note was made from
    (`Reads.kept`), None when there is none; `paperless` is Paperless's
    public address today, which a document's "Open in Paperless" links.

    A type the table does not know shows its own name as the label and no
    facts, so a new type reads as a plain card until it has a row here.

        >>> card = {"type": "bookmark", "path": "family/bookmarks/k.md", "meta": {
        ...     "date": "2026-10-11", "filed_by": "bart", "resource": "https://krusty.example/menu"}}
        >>> h = head_of(card, names={"bart": "Bart"}, today=date(2026, 11, 10), language="en")
        >>> [p.text for p in h.facts], h.outside.label
        (['krusty.example', 'saved by Bart', '11 October'], 'Open the page')
    """
    kind = str(card.get("type") or "")
    w = words(language)
    facts_of = _TYPES.get(kind, _plain)
    ctx = _Context(card, card.get("meta") or {}, names, today, language, w, kept or {}, paperless)
    icon = _diary_icon(ctx) if kind == "diary" else _ICONS.get(kind, kind) if kind in _TYPES else "note"
    label = w["types"].get(kind, kind.capitalize())
    if found := file_of(card):
        icon = "diary-photo" if found.is_image else "clip"
        label = f"{label} · {kind_label(found.kind, w)}"
        facts, extra, outside = _from_file(ctx, found)
    else:
        facts, extra, outside = facts_of(ctx)
    return Head(icon, label, [p for p in facts if p.text], [p for p in extra if p.text], outside)


# ── What a type reads ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Context:
    card: Mapping
    meta: Mapping
    names: Mapping[str, str]
    today: date
    language: str
    w: dict
    kept: Mapping
    paperless: str = ""

    def text(self, key: str) -> str:
        value = self.meta.get(key)
        return str(value).strip() if value is not None else ""

    def day(self, key: str = "date") -> str:
        """The card's `key` as a date in plain words, "" without one."""
        day = day_of(self.meta.get(key))
        return long_day(day, today=self.today, language=self.language) if day else ""

    def people(self, key: str = "persons") -> str:
        return ", ".join(self.name(p) for p in _strings(self.meta.get(key)))

    def name(self, person: str) -> str:
        """A person by the name a reader knows; an id nobody declared, capitalised."""
        return self.names.get(person.lower()) or (person[:1].upper() + person[1:])

    def links_under(self, folder: str) -> list[tuple[str, str]]:
        """(label, page address) of each link in the card's text to a file
        under `folder` (`correspondents/`, `documents/`), in text order."""
        found = []
        here = str(self.card.get("path") or "")
        for label, href in _LINK.findall(str(self.card.get("text") or "")):
            path = vault_path(href, here)
            if f"{folder}/" in f"/{path}" and path.endswith(".md"):
                found.append((label, card_address(path)))
        return found


Facts = tuple[list[Part], list[Part], Outside | None]


def _document(c: _Context) -> Facts:
    correspondent = c.text("correspondent")
    linked = dict(c.links_under("correspondents"))
    facts = [Part(correspondent, linked.get(correspondent, "")), Part(c.day()),
             Part(c.text("document_type"))]
    page = paperless_page(c.paperless, number) if (number := c.text("paperless_id")) else ""
    outside = Outside(c.w["open"]["paperless"], page, "Paperless", _short(page)) if page else None
    return facts, [Part(c.text("category"))], outside


def _bookmark(c: _Context) -> Facts:
    url = c.text("resource") or c.text("url")
    host = _host(url)
    by = c.text("filed_by")
    facts = [Part(host), Part(c.w["saved_by"].format(name=c.name(by)) if by else ""), Part(c.day())]
    outside = Outside(c.w["open"]["page"], url, host, _address(url)) if host else None
    return facts, [], outside


def _note(c: _Context) -> Facts:
    chat = bool(c.text("capture_id") or c.card.get("sources"))
    return [Part(c.people()), Part(c.day())], [Part(c.w["in_chat"] if chat else "")], None


def _from_file(c: _Context, found: FileNote) -> Facts:
    """A note made from a file: who saved it, the room it was saved in (the
    topic whose folder holds it), the day; the file's panel when the media
    store has it. A photo shows itself, so it has no outside link."""
    sources = c.card.get("sources") or [{}]
    by = str(c.card.get("filed_by") or sources[0].get("person") or "")
    facts = [Part(c.name(by) if by else ""), Part(_room(c)), Part(c.day())]
    return facts, [], None if found.is_image else _file_panel(c, found)


def _file_panel(c: _Context, found: FileNote) -> Outside | None:
    """The panel that opens the file, named by the media store's record:
    "uno-rules.pdf", "PDF · 240 KB". None without a record."""
    if not c.kept:
        return None
    name = str(c.kept.get("filename") or found.name)
    size = int(c.kept.get("size") or 0)
    kind = kind_label(found.kind, c.w)
    about = f"{kind} · {size_label(size, language=c.language)}" if size else kind
    return Outside(c.w["open"].get(found.kind, c.w["open"]["file"]), media_address(found.path),
                   name, about)


def _room(c: _Context) -> str:
    """The topic a note was saved under, by its name: a note saved in a
    topic's room lives in that topic's folder (`family/games/notes/...`)."""
    parts = str(c.card.get("path") or "").split("/")
    if len(parts) < 3 or parts[2] != "notes":
        return ""
    labels = c.card.get("tag_labels") or {}
    return str(labels.get(parts[1]) or "")


def _email(c: _Context) -> Facts:
    people = c.people()
    sender = _sender(c.meta)
    facts = [Part(c.w["from"].format(sender=sender) if sender else ""), Part(c.day())]
    extra = [Part(c.w["to"].format(names=people) if people else "")]
    extra += [Part(label, href, clip=True) for label, href in c.links_under("documents")]
    return facts, extra, None


def _sender(meta: Mapping) -> str:
    """An email's sender as the page names it, from the card's optional
    fields `from_name` and `from_address`: "the school office
    (office@school.example)", the name or the address alone when only one
    is there, "" without either.

    Only the fields count: a card without them shows no sender, and nothing
    is guessed from its text or its source. Emails filed before the
    archivist wrote the fields keep none, and their pages show the date
    alone.
    """
    name = str(meta.get("from_name") or "").strip()
    address = str(meta.get("from_address") or "").strip()
    return f"{name} ({address})" if name and address else name or address


def _diary(c: _Context) -> Facts:
    """For whom (the addressee as said, else the people), the day, and what
    the entry holds (`entry.holds`). Its media play in its sections."""
    addressee = c.text("addressee")
    whom = c.w["for"].format(name=c.name(addressee)) if addressee else c.people()
    held = holds(sections_of(c.card), language=c.language)
    extra = [Part(c.w["said_date"] if c.text("date_basis") == "spoken" else "")]
    return [Part(whom), Part(c.day()), Part(held)], extra, None


def _list(c: _Context) -> Facts:
    """A list or a month of its archive: its kind and topics; how many
    items are open, or for a calendar list its next date. The sections and
    items are the page's body (`lists.py`)."""
    kind = c.text("kind") or "todo"
    tags = c.card.get("tag_labels") or {}
    topics = ", ".join(str(tags.get(t, t)) for t in _strings(c.meta.get("topics")))
    extra = [Part(c.w["open_items"].format(n=open_count(c.card)) if kind != "archive" else "")]
    if kind == "calendar" and (next_one := coming(c.card, today=c.today)):
        item, day = next_one
        extra = [Part(c.w["next"].format(item=item, day=plain_day(day, today=c.today,
                                                                  language=c.language)))]
    return [Part(c.w["list_kinds"].get(kind, kind)), Part(topics)], extra, None


def _person(c: _Context) -> Facts:
    aliases = ", ".join(_strings(c.meta.get("aliases")))
    account = c.text("account")
    return [Part(c.w["also"].format(names=aliases) if aliases else ""),
            Part(f"@{account}" if account else "")], [], None


def _correspondent(c: _Context) -> Facts:
    aliases = ", ".join(_strings(c.meta.get("aliases")))
    website = c.text("website")
    outside = (Outside(c.w["open"]["web"], website, _host(website), _address(website))
               if website.startswith("http") else None)
    return [Part(c.w["also"].format(names=aliases) if aliases else "")], [Part(c.text("phone"))], outside


def _topic(c: _Context) -> Facts:
    count = int(c.card.get("links") or 0)
    cards = c.w["cards"][count != 1].format(n=count) if count else ""
    changes = c.card.get("changes") or []
    last = day_of(changes[0].get("day")) if changes else None
    changed = (c.w["changed_on"].format(day=plain_day(last, today=c.today, language=c.language))
               if last else "")
    return [Part(cards)], [Part(changed)], None


def _plain(c: _Context) -> Facts:
    return [Part(c.day())], [], None


_TYPES: dict[str, Callable[[_Context], Facts]] = {
    "document": _document, "bookmark": _bookmark, "note": _note, "email": _email,
    "diary": _diary, "list": _list, ARCHIVE: _list, "person": _person,
    "correspondent": _correspondent, "topic": _topic,
}

# A month of a list's archive is drawn as the list it came from.
_ICONS = {ARCHIVE: "list"}
# A diary entry's icon says what it was recorded as.
_DIARY_ICONS = {"voice": "diary-voice", "image": "diary-photo", "video": "diary-video",
                "text": "diary-text", "file": "document"}
# A markdown link in a card's text: (label, target).
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _diary_icon(c: _Context) -> str:
    return _DIARY_ICONS.get(c.text("kind") or "text", "diary-text")


# ── Small readers ──────────────────────────────────────────────────────────

def _host(url: str) -> str:
    return urlsplit(url).netloc.removeprefix("www.") if url.startswith("http") else ""


def _address(url: str) -> str:
    """The address as a reader sees it: no scheme, no `www.`."""
    split = urlsplit(url)
    return (split.netloc.removeprefix("www.") + split.path.rstrip("/")) or url


def _short(url: str) -> str:
    """An address shortened to its path, `…/<path>`: the site is named beside it."""
    path = urlsplit(url).path.rstrip("/")
    return f"…{path}" if path else url


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(v) for v in value or [] if str(v).strip()] if isinstance(value, list) else []
