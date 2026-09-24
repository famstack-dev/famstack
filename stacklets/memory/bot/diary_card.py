"""Diary records: one card in the vault per entry.

The compiler in `diary.py` turns the memories room into entries. This
module turns each entry into a card, a vault record of `type: diary`,
and a card back into an entry. The card is the source: the diary pages
and every other warm view of the family's memories are compiled from
cards, never from the room directly. That is what makes a correction
stick. A person fixes a misheard name or a wrong day on the card, and
the next compile shows the fix everywhere and leaves the card alone.

A card is raw on purpose, shaped like a captured note: frontmatter a
machine reads, the briefing callout (summary and facts), the quotes and
replies, and the words the family recorded. It is not the page the
family reads; `diary.pages_for` renders that from it.

Pure: no Matrix, no model, no Forgejo. `cli/diary.py` does the I/O.

    to_card(entry, extraction, ...)   entry + what a model read -> Card
    render(card) / parse(text)        Card <-> the file in the vault
    to_entry(card)                    Card -> the entry the pages render
    card_path(card, bucket=)          where the file lives
    edited_by_hand(text)              has a person changed this file?
    plan(existing, cards, ...)        which files a compile writes
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path

import diary
from stack.briefing import read_briefing, render_briefing
from stack.frontmatter import dump as frontmatter_dump
from stack.frontmatter import parse as frontmatter_parse
from stack.media import safe_name

DIARY_TYPE = "diary"
ENTRIES_DIR = "entries"


# ── What a model reads out of an entry ────────────────────────────────


@dataclass(frozen=True)
class Extraction:
    """The machine half of a card: what a model read out of the entry.

    Everything here is generated and may be wrong, which is why it all
    lands on a card a person can correct. Empty is valid: an entry the
    model could not read still becomes a record, with a plain title.
    `quotes` are passages the model copied; they are checked against the
    entry's words again whenever a page renders them.
    """

    title: str = ""
    description: str = ""
    persons: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    facts: list[str] = field(default_factory=list)
    quotes: list[str] = field(default_factory=list)
    model: str = ""
    # A day a family member's correction states, as YYYY-MM-DD. Honoured
    # only for an entry that has corrections (see `to_card`).
    date: str = ""
    # Whom the entry is spoken to, as a correction states it. Only
    # `correct` uses it; a first reading takes the addressee from the room.
    addressee: str = ""


_MAX_QUOTES = 3
_LIST_MARKER = re.compile(r"^(?:\s*[-*\u2022]\s+)+")


def _strings(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [" ".join(v.split()) for v in value if isinstance(v, str) and v.strip()]


# Labels that say what kind of thing a fact is rather than whom or what it
# is about. Models reach for them when asked for "Label: value", and the
# result restates the summary, the date or the people as a form.
_KIND_LABELS = {"action", "actions", "activity", "detail", "details", "event",
                "events", "date", "year", "time", "speaker", "recipient",
                "person", "persons", "people", "note"}


def _kind_only(fact: str) -> bool:
    label, sep, _ = fact.partition(":")
    return bool(sep) and label.strip().lower() in _KIND_LABELS


def _addressee(value, people: "dict[str, str]") -> str:
    """Whom a correction says the entry was for.

    A household member's name resolves to its canonical form; anyone else
    ("the kids", "Grandpa") is kept as the family wrote it. Nothing, or a
    model's null, is "".
    """
    if not isinstance(value, str):
        return ""
    words = " ".join(value.split())
    if not words or words.lower() in ("null", "none"):
        return ""
    return people.get(words.lower(), words)


def _iso_day(value) -> str:
    """`value` if it is a YYYY-MM-DD day, else ""."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        return ""
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return ""


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def extraction_from(raw, *, ontology, language: str,
                    people: "dict[str, str]", model: str) -> Extraction:
    """A model's answer about one entry, held to the household's vocabulary.

    `people` maps every known name, lowercased, to its canonical form
    (a person page's canonical name and synonyms). A name outside it is
    dropped: a mishearing must not become a family member. Topics go
    through the ontology's own canonicalisation, which also rejects a
    document type offered as a topic, and an unknown topic is dropped so
    the diary tags with the same vocabulary as documents. Tags are the
    topics, then `Person: <name>` for each person, as on a document.
    """
    if not isinstance(raw, dict):
        return Extraction(model=model)

    persons = _unique([people[n.lower()] for n in _strings(raw.get("persons"))
                       if n.lower() in people])
    topics: list[str] = []
    for offered in _strings(raw.get("topics")):
        resolved = ontology.canonicalize_topic(offered, language)
        if resolved.canonical:
            topics.append(resolved.canonical)
    topics = _unique(topics)

    def text(key: str) -> str:
        value = raw.get(key)
        return " ".join(value.split()) if isinstance(value, str) else ""

    return Extraction(
        title=text("title"),
        description=text("description"),
        persons=persons,
        tags=[*topics, *(f"Person: {p}" for p in persons)],
        summary=text("summary"),
        facts=[f for f in (_LIST_MARKER.sub("", f) for f in _strings(raw.get("facts")))
               if f and not _kind_only(f)],
        quotes=_strings(raw.get("quotes"))[:_MAX_QUOTES],
        model=model,
        date=_iso_day(raw.get("date")),
        addressee=_addressee(raw.get("addressee"), people),
    )


# ── The card ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Card:
    """One diary record, as it is kept in the vault.

    `entry_id` is the card's identity. It comes from the entry's first
    message, which never changes, so a recompile finds the same card
    whatever the model calls it this time. `at` is when that message
    reached the room, in epoch milliseconds.
    """

    entry_id: str
    on: date
    confidence: str
    kind: str
    sender: str
    at: int
    event_ids: list[str]
    resource: str = ""
    addressee: str = ""
    mode: str = "monologue"
    duration_ms: int | None = None
    media: list[str] = field(default_factory=list)
    body: str = ""
    replies: list[tuple[str, str]] = field(default_factory=list)
    title: str = ""
    description: str = ""
    persons: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    facts: list[str] = field(default_factory=list)
    quotes: list[str] = field(default_factory=list)
    model: str = ""


def entry_id_for(first_event_id: str) -> str:
    """The short, stable identity of an entry: a hash of its first message."""
    return hashlib.sha256(first_event_id.encode("utf-8")).hexdigest()[:10]


def to_card(entry: "diary.Entry", extraction: Extraction, *, room_id: str,
            media: "dict[str, str]") -> Card:
    """An entry and what a model read out of it, as a vault record.

    `media` maps event ids to the archived copy of each message's file,
    as `diary.pages_for` takes it; the card keeps the paths of this
    entry's own files.
    """
    first = entry.event_ids[0]
    # The room's evidence dates an entry: a spoken date, or the day it was
    # sent. A date the model offers is ignored here; only a family
    # member's correction can move it (`correct`).
    return Card(
        entry_id=entry_id_for(first),
        on=entry.on,
        confidence=entry.confidence,
        kind=entry.kind,
        sender=entry.sender,
        at=entry.at,
        event_ids=list(entry.event_ids),
        resource=f"https://matrix.to/#/{room_id}/{first}" if room_id else "",
        addressee=(entry.addressee or "").strip(),
        mode=entry.mode,
        duration_ms=entry.duration_ms,
        media=[media[e] for e in entry.event_ids if e in media],
        body=entry.body.strip(),
        replies=[(who, text.strip()) for who, text in entry.comments],
        title=extraction.title.strip() or diary.plain_title(entry),
        description=extraction.description.strip(),
        persons=list(extraction.persons),
        tags=list(extraction.tags),
        summary=" ".join(extraction.summary.split()),
        facts=[f.strip() for f in extraction.facts if f.strip()],
        quotes=[" ".join(q.split()) for q in extraction.quotes if q.strip()],
        model=extraction.model,
    )


def correct(card: Card, extraction: Extraction, *, event_id: str) -> Card:
    """The card after a family member's correction.

    `extraction` is the model's reading of the current card with the
    correction applied. It replaces everything the model reads out of an
    entry (title, description, people, tags, summary, facts, quotes) and,
    when the correction states them, the date and whom the entry was for. The family's words, replies and files
    stay as they are. The correction's event id joins the card's, so the
    card leads back to the message that changed it; the words of the
    correction are kept by the vault commit, not on the card.
    """
    on, confidence = card.on, card.confidence
    if extraction.date:
        on, confidence = date.fromisoformat(extraction.date), "corrected"
    return replace(
        card, on=on, confidence=confidence,
        addressee=extraction.addressee or card.addressee,
        title=extraction.title.strip() or card.title,
        description=extraction.description.strip(),
        persons=list(extraction.persons),
        tags=list(extraction.tags),
        summary=" ".join(extraction.summary.split()),
        facts=[f.strip() for f in extraction.facts if f.strip()],
        quotes=[" ".join(q.split()) for q in extraction.quotes if q.strip()],
        model=extraction.model or card.model,
        event_ids=[*card.event_ids, event_id] if event_id not in card.event_ids
        else list(card.event_ids),
    )


def to_entry(card: Card) -> "diary.Entry":
    """The entry a card describes, ready for the diary pages.

    The card's summary is the entry's narrative line and its quotes are
    re-checked against its words, so a person who edits the transcript
    cannot leave behind a quote that no longer appears in it.
    """
    return diary.Entry(
        on=card.on,
        confidence=card.confidence,
        basis=diary.basis_text(card.confidence),
        kind=card.kind,
        sender=card.sender,
        body=card.body,
        at=card.at,
        event_ids=list(card.event_ids),
        addressee=card.addressee or None,
        duration_ms=card.duration_ms,
        mode=card.mode,
        comments=list(card.replies),
        gist=card.summary,
        moments=diary.verify_moments(card.body, card.quotes),
        title=card.title,
    )


def card_path(card: Card, *, bucket: str) -> str:
    """Where the card lives: its day, then its identity.

    The title is left out on purpose. It is generated and correctable,
    and a path that followed it would move the file on every rename.

    Records sit under `entries/`, apart from the diary pages. The vault
    is mirrored into the wiki as it is, and a month's folder of records
    next to that month's page (`2026/03/` beside `2026/03.md`) would be
    two things at one address.
    """
    day = card.on
    return (f"{bucket}/{diary.DIARY_DIR}/{ENTRIES_DIR}/{day:%Y}/{day:%m}/"
            f"{day:%Y-%m-%d}-{card.entry_id}.md")


def media_of(cards) -> "dict[str, str]":
    """The event-id-to-file map `diary.pages_for` takes, from cards.

    The archive names every file, and every derived copy of it, after
    the event it came from (`stack.media.safe_name`), so a card needs
    to list only its paths.
    """
    out: dict[str, str] = {}
    for card in cards:
        stems = {safe_name(e): e for e in card.event_ids}
        for path in card.media:
            if (event_id := stems.get(Path(path).stem)) is not None:
                out[event_id] = path
    return out


# ── The file ──────────────────────────────────────────────────────────
#
# The family's words go last, under a heading of their own, so nothing
# in them can be read as a section of the card: replies are blockquoted
# and quotes are list items, and only the words are free text.

_BODY_HEADINGS = {"voice": "Transcript", "text": "Text"}
_CAPTION = "Caption"
_QUOTES = "## Quotes"
_REPLIES = "## Replies"
_DIGEST_LINE = re.compile(r"^digest: .*\n", re.MULTILINE)
_REPLY_HEAD = re.compile(r"^\*\*(.+)\*\*$")


def _iso_ms(ms: int) -> str:
    moment = datetime.fromtimestamp(ms / 1000, timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ms(iso: str) -> int:
    moment = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return round(moment.timestamp() * 1000)


def _frontmatter(card: Card) -> dict:
    fm: dict = {
        "type": DIARY_TYPE,
        "title": card.title,
        "date": card.on.isoformat(),
        "date_basis": card.confidence,
        "description": card.description,
        "persons": card.persons,
        "tags": card.tags,
        "filed_by": card.sender,
        "addressee": card.addressee,
        "medium": card.kind,
        "mode": card.mode,
    }
    if card.duration_ms:
        fm["duration_ms"] = int(card.duration_ms)
    fm.update({
        "resource": card.resource,
        "entry_id": card.entry_id,
        "event_ids": card.event_ids,
        "media": card.media,
        "model": card.model,
        "timestamp": _iso_ms(card.at),
    })
    return fm


def _meta_line(card: Card) -> str:
    bits = [f"**From** {card.sender}"]
    if card.addressee:
        bits.append(f"**For** {card.addressee}")
    bits.append(f"**Date** {card.on.isoformat()} ({card.confidence})")
    return "> " + " · ".join(bits)


def _unsigned(card: Card) -> str:
    lines = ["---", frontmatter_dump(_frontmatter(card)), "---", "",
             f"# {card.title}", "", _meta_line(card), ""]

    briefing = render_briefing(summary=card.summary, facts=card.facts,
                               action_items=None)
    if briefing:
        lines += [briefing, ""]

    for path in card.media:
        embed = card.kind in ("voice", "image", "video")
        lines += [f"![[{path}]]" if embed else f"[{Path(path).name}]({path})", ""]

    if card.quotes:
        lines += [_QUOTES, ""] + [f"- {q}" for q in card.quotes] + [""]

    if card.replies:
        lines += [_REPLIES, ""]
        for who, text in card.replies:
            lines.append(f"**{who}**")
            lines += [f"> {ln}" if ln else ">" for ln in text.splitlines()]
            lines.append("")

    if card.body:
        lines += [f"## {_BODY_HEADINGS.get(card.kind, _CAPTION)}", "", card.body, ""]

    return "\n".join(lines).rstrip() + "\n"


def _digest_of(text: str) -> str:
    """A hash of the file's content, blind to the digest line itself and
    to trailing whitespace an editor may add or trim on save."""
    unsigned = _DIGEST_LINE.sub("", text)
    normal = "\n".join(ln.rstrip() for ln in unsigned.splitlines()).strip()
    return hashlib.sha256(normal.encode("utf-8")).hexdigest()[:16]


def render(card: Card, *, family_owned: bool = False) -> str:
    """The card as a vault file, signed with the digest of its content.

    A card a family member corrected is written unsigned: it is theirs
    from then on, and the compile leaves it alone (`edited_by_hand`).
    """
    unsigned = _unsigned(card)
    if family_owned:
        return unsigned
    head, sep, rest = unsigned.partition("\n---\n")
    return f"{head}\ndigest: {_digest_of(unsigned)}{sep}{rest}"


def edited_by_hand(text: str) -> bool:
    """Whether a person has changed this file since the compiler wrote it.

    A file without a digest was written by a person or corrected by one
    (`render(..., family_owned=True)`), and a file whose content no longer
    matches its digest was changed after the compiler wrote it. Either
    way it is the family's, and the compiler leaves it alone.
    """
    stored = str(frontmatter_parse(text).get("digest") or "")
    return not stored or stored != _digest_of(text)


def _sections(body: str):
    """The quotes, replies and words of a card body."""
    quotes: list[str] = []
    said: dict[str, list[tuple[str, str]]] = {_REPLIES: []}
    words = ""
    lines = body.splitlines()
    heads = [f"## {h}" for h in (*_BODY_HEADINGS.values(), _CAPTION)]
    for i, ln in enumerate(lines):
        if ln.strip() in heads:
            words = "\n".join(lines[i + 1:]).strip("\n")
            lines = lines[:i]
            break

    section = ""
    for ln in lines:
        if ln.startswith("## "):
            section = ln.strip()
            continue
        if section == _QUOTES and ln.startswith("- "):
            quotes.append(ln[2:].strip())
        elif section in said:
            block = said[section]
            if m := _REPLY_HEAD.match(ln.strip()):
                block.append((m.group(1), ""))
            elif ln.startswith(">") and block:
                who, text = block[-1]
                part = ln[2:] if ln.startswith("> ") else ln[1:]
                block[-1] = (who, f"{text}\n{part}" if text else part)
    tidy = {k: [(who, text.strip("\n")) for who, text in v] for k, v in said.items()}
    return quotes, tidy[_REPLIES], words


def parse(text: str) -> Card:
    """A card file back into a Card, including whatever a person changed."""
    fm = frontmatter_parse(text)
    _, _, after = text.partition("\n---\n")
    summary, facts = read_briefing(after)
    quotes, replies, words = _sections(after)

    def listed(key: str) -> list[str]:
        value = fm.get(key) or []
        return [str(v) for v in value] if isinstance(value, list) else [str(value)]

    duration = fm.get("duration_ms")
    return Card(
        entry_id=str(fm.get("entry_id") or ""),
        on=date.fromisoformat(str(fm.get("date"))[:10]),
        confidence=str(fm.get("date_basis") or "sent"),
        kind=str(fm.get("medium") or "text"),
        sender=str(fm.get("filed_by") or ""),
        at=_ms(fm["timestamp"]) if fm.get("timestamp") else 0,
        event_ids=listed("event_ids"),
        resource=str(fm.get("resource") or ""),
        addressee=str(fm.get("addressee") or ""),
        mode=str(fm.get("mode") or "monologue"),
        duration_ms=int(duration) if duration else None,
        media=listed("media"),
        body=words,
        replies=replies,
        title=str(fm.get("title") or ""),
        description=str(fm.get("description") or ""),
        persons=listed("persons"),
        tags=listed("tags"),
        summary=summary,
        facts=facts,
        quotes=quotes,
        model=str(fm.get("model") or ""),
    )


# ── Which files a compile writes ──────────────────────────────────────


@dataclass
class Plan:
    """What a compile does to the vault's diary tree.

    `writes` maps a path to its new content, `deletes` lists files to
    remove, and `kept` lists files a person edited that the compile
    would otherwise have changed or removed. Those are reported, never
    touched.
    """

    writes: dict[str, str] = field(default_factory=dict)
    deletes: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)


def plan(existing: "dict[str, str]", cards, *, bucket: str,
         complete: bool) -> Plan:
    """Compare the vault's diary files with a compile's cards.

    `existing` maps each diary file in the vault to its content. Files
    are matched to cards by `entry_id`, not by path, so a card whose day
    changed is moved rather than duplicated. `complete` says the compile
    read the whole room; only then does a card with no entry mean its
    messages are gone.
    """
    on_disk: dict[str, tuple[str, str]] = {}
    for path, text in existing.items():
        fm = frontmatter_parse(text)
        if fm.get("type") == DIARY_TYPE and fm.get("entry_id"):
            on_disk[str(fm["entry_id"])] = (path, text)

    out = Plan()
    seen: set[str] = set()
    for card in cards:
        seen.add(card.entry_id)
        path, text = card_path(card, bucket=bucket), render(card)
        if card.entry_id not in on_disk:
            out.writes[path] = text
            continue
        old_path, old_text = on_disk[card.entry_id]
        if edited_by_hand(old_text):
            # The digest a hand-edited file still carries is the one the
            # compiler signed it with, so it says whether the room has
            # moved on since. Only then is there anything to report. A
            # file with no digest (written or corrected by the family)
            # is theirs by design and kept without a word.
            signed = str(frontmatter_parse(old_text).get("digest") or "")
            if signed and signed != _digest_of(text):
                out.kept.append(old_path)
            continue
        if old_path != path:
            out.deletes.append(old_path)
            out.writes[path] = text
        elif old_text != text:
            out.writes[path] = text

    if complete:
        for entry_id, (path, text) in on_disk.items():
            if entry_id in seen:
                continue
            if edited_by_hand(text):
                out.kept.append(path)
            else:
                out.deletes.append(path)
    return out
