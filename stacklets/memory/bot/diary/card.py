"""Diary records: one card in the vault per entry, and its sidecar.

The compiler (`entries`) turns the memories room into entries. This
module turns each entry into a card, a vault record of `type: diary`,
and a card back into an entry. The card is the source: the diary pages
and every other warm view of the family's memories are compiled from
cards, never from the room directly. That is what makes a correction
stick. A person fixes a misheard name or a wrong day on the card, and
the next compile shows the fix everywhere and leaves the card alone.

A card keeps its sources as a note does (ADR-018): what the entry is
about (its frontmatter, the briefing callout, the model's quotes), then
`## Sources`, one blockquote per message and per reply in the order
sent, a gallery one per file. The sidecar `<stem>.json` beside it says
who sent each one, when, its event id and its file's hash, and how the
card was made. It is not the page the family reads; `pages.pages_for`
renders that from it.

    > ![[/media/2026/11/p1.jpg]]          Homer's photo, with its caption
    > Grampa at the gate

    > Grampa came for lunch today.        Homer's line under it

    > He left his hat here.               Marge's reply, the next day

Pure: no Matrix, no model, no Forgejo. The command (`bot/cli/diary/`)
does the I/O. A card in the format before ADR-018 is converted by
migration 0012.

    to_card(entry, extraction, ...)        entry + what a model read -> Card
    render(card) / render_sidecar(card)    Card -> the card and its sidecar
    parse(text, sidecar)                   the two files -> Card
    files(card, bucket=)                   both files, by their paths
    to_entry(card)                         Card -> the entry the pages render
    card_path(card, bucket=)               where the card lives
    edited_by_hand(text)                   has a person changed this file?
    header_on_file(path, text)             its fields, or None when they do not parse
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone

from stack.briefing import is_short_text, read_briefing, render_briefing
from stack.cards import CARD_ID, card_id_from
from stack.cards.sources import (NOTE, ORIGINAL, REPLY, UNKNOWN, Source, SourcesMismatch,
                                 quotes_of, read_sidecar, render_sources, sidecar_path,
                                 sidecar_text, source_from, sources_of)
from stack.digest import changed_by_hand, signed
from stack.frontmatter import dump as frontmatter_dump
from stack.frontmatter import FrontmatterError
from stack.frontmatter import parse as frontmatter_parse

from .dating import basis_text
from .entries import Entry, verify_moments
from .extraction import Extraction
from .pages import DIARY_DIR
from .sources import sources_of_parts
from .texts import plain_title

DIARY_TYPE = "diary"
ENTRIES_DIR = "entries"


# ── The card ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Card:
    """One diary record, as it is kept in the vault: a card and its sidecar.

    `sources` are what the entry was made from, one per message and per
    reply in the order sent, a gallery one per file (ADR-018 §2). Each is
    a quote under the card's `## Sources` and a record in the sidecar.
    Everything the entry is (who sent it, when, its words, its files,
    its replies) is read from them; the rest of the card is the model's
    reading, which a person may correct.

    `entry_id` is the card's identity: a hash of its first message, which
    never changes, so a recompile finds the same card whatever the model
    calls it this time. `card_id` is the card's own id in the vault
    (ADR-016), made when the card is first written (`plan`). `confidence`
    is how its day was found and `mode` how its words were spoken; both
    are kept on the first source. `model` and `prompt_template` say how
    the reading was made, in the sidecar's `made`.

        card = to_card(entry, extraction, media={"$p1": "/media/2026/11/p1.w1600.jpg"})
        [s.person for s in card.sources]      # ['homer', 'homer', 'marge']
        card.body                             # 'Grampa visited us on Halloween'
        card.replies                          # [('marge', 'So cute!')]
    """

    entry_id: str
    on: date
    confidence: str
    kind: str
    sources: list[Source] = field(default_factory=list)
    addressee: str = ""
    mode: str = "monologue"
    title: str = ""
    description: str = ""
    persons: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    facts: list[str] = field(default_factory=list)
    quotes: list[str] = field(default_factory=list)
    model: str = ""
    card_id: str = ""
    prompt_template: str = ""

    @property
    def originals(self) -> list[Source]:
        """The entry's own messages, in order."""
        return [s for s in self.sources if s.role == ORIGINAL]

    @property
    def sender(self) -> str:
        """Who sent the entry's first message; "" for a card without one."""
        first = (self.originals or self.sources or [None])[0]
        return first.person if first is not None and first.person != UNKNOWN else ""

    @property
    def at(self) -> int:
        """When the first message reached the room, in epoch milliseconds, to the
        second as its source keeps it; 0 when unknown."""
        first = (self.originals or [None])[0]
        return _ms(first.time) if first is not None and first.time != UNKNOWN else 0

    @property
    def event_ids(self) -> list[str]:
        """Every message the card was made from, corrections included, each once, in order."""
        return list(dict.fromkeys([s.id for s in self.sources if s.id != UNKNOWN]))

    @property
    def media(self) -> list[str]:
        """The archived files the card embeds or links, in the order sent."""
        return [s.media_link for s in self.sources if s.role != NOTE and s.media_link]

    @property
    def body(self) -> str:
        """The words of the entry's own messages, a recording cut in two read back as one."""
        return _joined([s.words for s in self.originals])

    @property
    def replies(self) -> list[tuple[str, str]]:
        """Each reply as who wrote it and their words."""
        return [(s.person, s.words) for s in self.sources if s.role == REPLY]

    @property
    def duration_ms(self) -> int | None:
        """The length of the entry's recordings, None when it has none."""
        lengths = [s.duration_ms for s in self.originals if s.duration_ms]
        return sum(lengths) if lengths else None


def entry_id_for(first_event_id: str) -> str:
    """The short, stable identity of an entry: a hash of its first message."""
    return hashlib.sha256(first_event_id.encode("utf-8")).hexdigest()[:10]


def _summary(words: str, extraction: Extraction) -> str:
    """The model's summary of an entry, or none for an entry too short for one.

    A short entry's card shows its words, and a summary of them says the
    same again at the same length. The prompt asks for none; this holds
    the card to it whatever the model wrote.
    """
    return "" if is_short_text(words) else " ".join(extraction.summary.split())


def to_card(entry: "Entry", extraction: Extraction, *, media: "dict[str, str]",
            media_sha: Callable[[str], str] = lambda link: "") -> Card:
    """An entry and what a model read out of it, as a vault record.

    `media` maps each file's artifact id to its archived copy, as
    `pages_for` takes it. `media_sha(link)` is the hash the media
    store recorded for a file, "" for none. Each part of the entry
    becomes a source (`sources_of_parts`). The entry must carry its
    parts, as every entry the compile builds does; refuses one without
    (`ValueError`).
    """
    if not entry.parts:
        raise ValueError(f"the entry of {entry.event_ids[:1]} carries no parts")
    first = entry.event_ids[0]
    sources = sources_of_parts(entry.parts, media=media, media_sha=media_sha)
    # The room's evidence dates an entry: a spoken date, or the day it was
    # sent. A date the model offers is ignored here; only a family
    # member's correction can move it (`correct`).
    return Card(
        entry_id=entry_id_for(first),
        on=entry.on,
        confidence=entry.confidence,
        kind=entry.kind,
        sources=sources,
        addressee=(entry.addressee or "").strip(),
        mode=entry.mode,
        title=extraction.title.strip() or plain_title(entry),
        description=extraction.description.strip(),
        persons=list(extraction.persons),
        tags=list(extraction.tags),
        summary=_summary(entry.body, extraction),
        facts=[f.strip() for f in extraction.facts if f.strip()],
        quotes=[" ".join(q.split()) for q in extraction.quotes if q.strip()],
        model=extraction.model,
        prompt_template=extraction.template,
    )


def correct(card: Card, extraction: Extraction, *, event_id: str, by: str,
            words: str, time: str) -> Card:
    """The card after a family member's correction.

    `extraction` is the model's reading of the current card with the
    correction applied. It replaces everything the model reads out of an
    entry (title, description, people, tags, summary, facts, quotes) and,
    when the correction states them, the date and whom the entry was for.
    The family's words, replies and files stay as they are. The correction
    joins the sources as a `note` by `by`: what a person gave the model to
    read the card again (ADR-018 §3), with its event id, so the card leads
    back to the message that changed it and applying it twice changes
    nothing. It is no part of the entry, so the pages do not show it.
    """
    on, confidence = card.on, card.confidence
    if extraction.date:
        on, confidence = date.fromisoformat(extraction.date), "corrected"
    sources = list(card.sources)
    if event_id not in card.event_ids:
        sources.append(Source(NOTE, by, time, "chat", "text", event_id, quote=words.strip()))
    return replace(
        card, on=on, confidence=confidence,
        addressee=extraction.addressee or card.addressee,
        title=extraction.title.strip() or card.title,
        description=extraction.description.strip(),
        persons=list(extraction.persons),
        tags=list(extraction.tags),
        summary=_summary(card.body, extraction),
        facts=[f.strip() for f in extraction.facts if f.strip()],
        quotes=[" ".join(q.split()) for q in extraction.quotes if q.strip()],
        model=extraction.model or card.model,
        sources=sources,
    )


def to_entry(card: Card) -> "Entry":
    """The entry a card describes, ready for the diary pages.

    The card's summary is the entry's narrative line and its quotes are
    re-checked against its words, so a person who edits the transcript
    cannot leave behind a quote that no longer appears in it.
    """
    return Entry(
        on=card.on,
        confidence=card.confidence,
        basis=basis_text(card.confidence),
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
        moments=verify_moments(card.body, card.quotes),
        title=card.title,
        entry_id=card.entry_id,
        card_id=card.card_id,
    )


def card_path(card: Card, *, bucket: str) -> str:
    """Where the card lives: its day, then its identity. Its sidecar is
    beside it (`stack.cards.sources.sidecar_path`).

    The title is left out on purpose. It is generated and correctable,
    and a path that followed it would move the file on every rename.

    Records sit under `entries/`, apart from the diary pages. The vault
    is mirrored into the wiki as it is, and a month's folder of records
    next to that month's page (`2026/03/` beside `2026/03.md`) would be
    two things at one address.
    """
    day = card.on
    return (f"{bucket}/{DIARY_DIR}/{ENTRIES_DIR}/{day:%Y}/{day:%m}/"
            f"{day:%Y-%m-%d}-{card.entry_id}.md")


def _joined(parts) -> str:
    """Words of several messages as one, as the compile joins them
    (`_joined_body`): a recording cut in two reads as one sentence."""
    kept = [p.strip() for p in parts if p.strip()]
    if len(kept) < 2:
        return kept[0] if kept else ""
    return " ".join(kept)


# ── The file ──────────────────────────────────────────────────────────
#
# The card keeps what the entry is about: its fields, its title, the
# meta line, the model's summary and facts and quotes, then `## Sources`,
# one quote per source. The sidecar beside it keeps where each source
# came from (ADR-018 §2, §3).

_QUOTES = "## Quotes"


def _iso_ms(ms: int) -> str:
    moment = datetime.fromtimestamp(ms / 1000, timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ms(iso: str) -> int:
    moment = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return round(moment.timestamp() * 1000)


def _frontmatter(card: Card) -> dict:
    return {
        "type": DIARY_TYPE,
        CARD_ID: card.card_id,
        "title": card.title,
        "date": card.on.isoformat(),
        "description": card.description,
        "persons": card.persons,
        "tags": card.tags,
        "addressee": card.addressee,
        "kind": card.kind,
    }


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

    if card.quotes:
        lines += [_QUOTES, ""] + [f"- {q}" for q in card.quotes] + [""]

    lines += [render_sources(card.sources), ""]
    return "\n".join(lines).rstrip() + "\n"


def render(card: Card, *, family_owned: bool = False) -> str:
    """The card as a vault file, signed with the digest of its content.

    A card a family member corrected is written unsigned: it is theirs
    from then on, and the compile leaves it alone (`edited_by_hand`).
    The sidecar is `render_sidecar`'s.
    """
    unsigned = _unsigned(card)
    return unsigned if family_owned else signed(unsigned)


def render_sidecar(card: Card) -> str:
    """The card's sidecar, as written: each source's record, the first
    original with how the day was found and how the words were spoken,
    and `made` with the model and the prompt template.

        render_sidecar(card)
        # '{"card_id": "diary-2f8c6d0e7b", "filed_by": "homer", "sources": [...], "made": {...}}'
    """
    sources = list(card.sources)
    first = next((n for n, s in enumerate(sources) if s.role == ORIGINAL), None)
    if first is not None:
        sources[first] = replace(sources[first], date_basis=card.confidence, mode=card.mode)
    return sidecar_text(card_id=card.card_id, filed_by=card.sender, sources=sources,
                        made={"model": card.model, "prompt_template": card.prompt_template})


def files(card: Card, *, bucket: str, family_owned: bool = False) -> "dict[str, str]":
    """The two files a card is written as, by path: the card and its sidecar.
    They are written in one commit, or neither (ADR-018 §3)."""
    path = card_path(card, bucket=bucket)
    return {path: render(card, family_owned=family_owned),
            sidecar_path(path): render_sidecar(card)}


def edited_by_hand(text: str) -> bool:
    """Whether a person has changed this file since the compiler wrote it.

    A file without a digest was written by a person or corrected by one
    (`render(..., family_owned=True)`), and a file whose content no longer
    matches its digest was changed after the compiler wrote it. Either
    way it is the family's, and the compiler leaves it alone.
    """
    return changed_by_hand(text)


def header_on_file(path: str, text: str) -> "dict | None":
    """The fields of the card at `path`, or None when its header does not parse.

    Only a hand edit can break a diary card's header. The diary's readers
    leave such a card out instead of stopping, because one broken file
    would otherwise stop every compile, correction and page until a person
    repaired it. It is named on stderr with its path, so it can be found;
    the diary neither repairs nor rewrites it.

        header_on_file("family/diary/entries/2026/10/2026-10-01-ab12.md", text)
        # {"type": "diary", "title": ...}, or None and one line on stderr
    """
    try:
        return frontmatter_parse(text)
    except FrontmatterError as e:
        print(f"[diary] {path}: frontmatter does not parse ({e}), left out",
              file=sys.stderr)
        return None


def parse(text: str, sidecar: str = "") -> Card:
    """A card file and its sidecar back into a Card, including whatever a
    person changed in the card.

    A quote a person added or removed by hand pairs with the records in
    order; a quote left over is the sender's, a record left over keeps no
    words. A card without a sidecar (one a person wrote by hand) keeps its
    quotes as the words of a sender nobody recorded, so the pages still
    show them, and has no entry id: the compile does not take it for one
    of its entries.
    """
    fm = frontmatter_parse(text)
    _, _, after = text.partition("\n---\n")
    summary, facts = read_briefing(after)
    side = read_sidecar(sidecar)
    sources = diary_sources(text, side)
    originals = [s for s in sources if s.role == ORIGINAL]
    first = originals[0] if originals else None
    made = side.get("made") if isinstance(side.get("made"), dict) else {}

    def listed(key: str) -> list[str]:
        value = fm.get(key) or []
        return [str(v) for v in value] if isinstance(value, list) else [str(value)]

    return Card(
        entry_id=entry_id_for(first.id) if first is not None and first.id != UNKNOWN else "",
        on=date.fromisoformat(str(fm.get("date"))[:10]),
        confidence=(first.date_basis if first is not None else "") or "sent",
        kind=str(fm.get("kind") or "text"),
        sources=sources,
        addressee=str(fm.get("addressee") or ""),
        mode=(first.mode if first is not None else "") or "monologue",
        title=str(fm.get("title") or ""),
        description=str(fm.get("description") or ""),
        persons=listed("persons"),
        tags=listed("tags"),
        summary=summary,
        facts=facts,
        quotes=_listed_quotes(after),
        model=str(made.get("model") or ""),
        card_id=card_id_from(fm),
        prompt_template=str(made.get("prompt_template") or ""),
    )


def diary_sources(text: str, side: dict) -> list[Source]:
    """A diary card's sources: its quotes with the sidecar's records, in
    order (`sources_of`), also when a person added or removed a quote; a
    quote with no record is the words of a sender nobody recorded. The
    pages and the cards API read a diary card's sources through it.

        diary_sources("## Sources\n\n> Hi\n", {})[0].quote   # 'Hi', by 'unknown'
    """
    try:
        return sources_of(text, side)
    except SourcesMismatch:
        pass
    records = [r for r in side.get("sources") or [] if isinstance(r, dict)]
    quotes = quotes_of(text)
    paired = [source_from(r, q) for r, q in zip(records, quotes)]
    sender = str(side.get("filed_by") or UNKNOWN)
    paired += [Source(ORIGINAL, sender, UNKNOWN, "cards", "text", UNKNOWN, quote=q,
                      reconstructed=True) for q in quotes[len(records):]]
    paired += [source_from(r, "") for r in records[len(quotes):]]
    return paired


def _listed_quotes(body: str) -> list[str]:
    """The model's quotes: the list items under `## Quotes`."""
    lines = body.splitlines()
    try:
        start = lines.index(_QUOTES) + 1
    except ValueError:
        return []
    quotes = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.startswith("- "):
            quotes.append(line[2:].strip())
    return quotes
