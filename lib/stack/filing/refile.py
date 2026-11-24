"""Refiling a note: a correction files the note again from every one of its sources.

    note = NoteRead(path, card.text, card.version, side.text, side.version, history)
    sources = refile_sources(card_sources(card.text, sidecar), history=history,
                             added=[reply])
    refiling = refiling_of(note, sources)
    filing = await refile(note, refiling, policy, reader=reading, writer=mirror,
                          directory=vault, by="@lisa:simpson")

A note's sources are its record (ADR-018): the messages it was sent as
(each an `original`), the replies and notes added since, and a person's
hand edits from the card's history. A correction adds one source and files
the note again from all of them:

    caller (the chat, `stack memory reprocess`)
      │ NoteRead       card, sidecar and history, read once, with versions
      │ refile_sources the sources, with the correction added by its id
      │ refiling_of    every original's words and photos, the form, the filer
      │ refile         `stack.filing.file`, written on the versions read first
      ▼
    Filing: filed, or not changed (why)

Why one read, one builder and one place: the chat and the command line
each built a note's sources their own way (one compared a reply's words,
the other its id), each read the card again at write time, and four places
read only the first original. A note of a photo, a voice message and a line
was rebuilt from the photo alone, a second reply with the same words was
dropped, and a keyword added while the model answered was written over.

- **Every original is read.** The model reads the words of every
  original, in order, and sees every photo (`originals`).
- **The form is read from the card.** A card whose interpretation is a
  `> [!summary]` callout stays a summary; any other is a clean version,
  of any length, and is rebuilt as one.
- **A reply is a source by its id.** The note holds each message once; two
  replies with the same words are two sources.
- **The write is on the versions read first.** A change since then (a hand
  edit while the model answers, a second correction at once) refuses the
  write as `changed`, and nothing of it is lost.

What it does not do: read the memory or the chat (the caller hands in what
it read), ask the model (the caller's `Reader`), or decide what a refused
write tells the person.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from ..cards.events import Correction, Event, in_order, name_of
from ..cards.history import CardHistory
from ..cards.sources import (
    NOTE, ORIGINAL, REPLY, UNKNOWN, Source, corrections_of, interpretation_of, read_sidecar,
    with_quote_edits, words_of,
)
from ..frontmatter import FrontmatterError, parse
from .capture import Capture, KeptTags, Origin, Refile
from .file import Filing, Directory, Reader, Writer, file
from .policy import Policy

__all__ = ["NoteRead", "Rebuild", "Refiling", "originals", "refile", "refile_sources", "refiling_of"]

# How a card shows a summary in place of a clean version (`stack.filing.cards`).
SUMMARY_CALLOUT = "> [!summary]"


# ── The read ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NoteRead:
    """A note as its refile reads it, once, before the model is asked.

    ``version`` and ``sidecar_version`` are the files' blob ids
    (`File.version`, "" for a sidecar that is not there); the refile is
    written on them. ``history`` is what the card's history gives back
    (`stack.cards.history.history_of`), None for a card the curator has no
    history of: a note without a card id, or one it does not know (404).

        NoteRead("marge/notes/2026/11/grill-charcoal-7e67c9.md", card.text, card.version,
                 side.text, side.version, history)
    """

    path: str
    text: str
    version: str
    sidecar: str = ""
    sidecar_version: str = ""
    history: CardHistory | None = None

    @property
    def fields(self) -> dict:
        """The card's frontmatter; {} when it does not parse."""
        try:
            return parse(self.text)
        except FrontmatterError:
            return {}


@dataclass(frozen=True)
class Rebuild:
    """What a corrected note is rebuilt from (ADR-018 §7).

    It holds five things:

    - the name of the note's writer;
    - the clean version that the events apply to;
    - the corrections and edits of people, in time order (`stack.cards.events`);
    - the card's fields as the card holds them (`title`, `tags`, `persons`,
      `date`), which keep their values where no event names them;
    - the clean version that the card shows now, with the edits of people.

    An answer without a new text keeps the last one.

        Rebuild("Homer", "Camping trip next weekend: ...",
                (Correction("homer", "2026-10-20T09:00:00+00:00", "we leave at 5, not 4"),),
                {"title": "Camping trip", "tags": ["camping"], "persons": ["homer"]})
    """

    writer: str
    prior: str
    events: tuple[Event, ...]
    fields: dict = field(default_factory=dict)
    current: str = ""

    def said(self) -> str:
        """Each event's message, one per line, as the model reads it."""
        return "\n".join(e.message() for e in self.events)

    @property
    def edited(self) -> bool:
        """Whether a person edited the clean version by hand."""
        return any(e.edits_the_text for e in self.events)


@dataclass(frozen=True)
class Refiling:
    """What a note is filed again from, read off its sources and its card.

    ``words`` are every original's words, in order; ``images`` the media
    link of every photo among them. ``clean`` says the card shows a clean
    version (no summary callout) and the note is filed as one again.
    ``rebuild`` is what such a note's corrections and edits rebuild it
    from, None when nobody corrected or edited it. ``filer`` is who filed
    the note, ``capture_id`` its first message's id. ``refused`` is why it
    cannot be refiled ("" when it can): `no_original`.

        refiling_of(note, sources).words   # 'The tent Bart wants\\n\\nCall the camping shop on Monday.'
    """

    sources: tuple[Source, ...]
    words: str
    clean: bool
    corrections: str
    rebuild: Rebuild | None
    images: tuple[str, ...]
    filer: str
    capture_id: str
    refused: str = ""


# ── The sources ───────────────────────────────────────────────────────


def refile_sources(held: Sequence[Source], *, history: CardHistory | None = None,
                   chat: Sequence[Source] = (), added: Sequence[Source] = ()) -> list[Source]:
    """What a refile of the note reads, in order: the one builder of a note's sources.

    ``held`` are the sources the card keeps (`memory.note_card.card_sources`).
    A person's hand edits of a quote are put back to the words as sent and
    appended as their notes (`with_quote_edits`, from ``history``). ``chat``
    are the note's messages as the chat has them now: one replaces an
    original the card keeps only as a reconstruction (the migration marked
    it, because the old card recorded it badly), and is the original of a
    note that holds none. ``added`` are the replies and notes since, each
    appended unless the note holds a source with its id; a bot's message is
    never one. A reply is told apart by its id alone, so the same words sent
    twice are two sources, and the same message handed over twice is one.

        refile_sources(held, added=[Source(REPLY, "marge", "…", "chat", "text", "$fix2",
                                           quote="Call it Barbecue")])
    """
    sources = with_quote_edits(held, history.quotes_as_sent, history.quote_edits) \
        if history else list(held)
    for message in chat:
        _put_back(sources, message)
    for source in added:
        _add(sources, source)
    return sources


def originals(sources: Sequence[Source]) -> list[Source]:
    """Every message the note was sent as, in order (role `original`).

        [s.id for s in originals(sources)]   # ['$tent', '$memo', '$line']: not the replies
    """
    return [s for s in sources if s.role == ORIGINAL]


def refiling_of(note: NoteRead, sources: Sequence[Source]) -> Refiling:
    """What `note` is filed again from, with `sources` (`refile_sources`).

    Reads every original: its words (joined, in order) and its photos. The
    form comes from the card, never from the words' length: a card whose
    interpretation is not a summary callout is a clean version, an empty
    one too, and stays one. The filer is the sidecar's `filed_by`, else the
    first original's sender. A note without an original is refused
    (`no_original`): it has nothing to be rebuilt from.

        refiling = refiling_of(note, sources)
        (refiling.clean, refiling.filer, refiling.capture_id)   # (True, 'marge', '$grill')
    """
    sent = originals(sources)
    first = sent[0] if sent else None
    words = words_of(sources)
    clean = bool(words.strip()) and not _shows_a_summary(note.text)
    return Refiling(
        sources=tuple(sources), words=words, clean=clean, corrections=corrections_of(sources),
        rebuild=_rebuild_of(first, sources, note.history, note.fields,
                            interpretation_of(note.text)) if clean and first else None,
        images=tuple(s.media_link for s in sent if s.kind == "image" and s.media_link),
        filer=_filer(read_sidecar(note.sidecar), first),
        capture_id=first.id if first else "",
        refused="" if first else "no_original")


# ── Filing it again ───────────────────────────────────────────────────


async def refile(note: NoteRead, refiling: Refiling, policy: Policy, *, reader: Reader,
                 writer: Writer, directory: Directory | None, by: str, content,
                 requested_by: str = "", own_tags: KeptTags | None = None,
                 channel: str = "chat") -> Filing:
    """File the note again from `refiling`, as `policy` places it, on the versions `note` was read at.

    ``reader`` is the caller's reading of the note (its model, shown
    ``content``: the words, or the title when there are none). ``by`` is
    who made the correction (the commit is theirs), ``requested_by`` who
    asked for this interpretation. The card keeps its place, its people
    when the answer names nobody, and a person's field edits from the
    history (`Refile.ops`). A write on a card or sidecar that changed
    since `note` was read is refused: `not_changed`, reason `changed`.

        filing = await refile(note, refiling, Policy(room_topics=("camping",)),
                              reader=reading, writer=mirror, directory=vault,
                              by="@lisa:simpson", content=words)
        filing.status    # 'filed'
    """
    fields = note.fields
    origin = Origin(channel=channel, person=refiling.filer, account=refiling.filer,
                    capture_id=refiling.capture_id or None)
    capture = Capture(kind="note", content=content, origin=origin, sources=list(refiling.sources),
                      captured_at=str(fields.get("date") or "") or None)
    again = Refile(note.path, by=by, own_tags=own_tags,
                   ops=note.history.ops if note.history else (),
                   requested_by=requested_by, people=tuple(_persons(fields)))
    return await file(capture, policy, reader=reader, writer=writer, directory=directory,
                      refile=again, base=note.version, sidecar_base=note.sidecar_version)


# ── Details ───────────────────────────────────────────────────────────


def _put_back(sources: list[Source], message: Source) -> None:
    """Read an original again from the chat where the card keeps only a
    reconstruction of it, or make it the original of a note that has none.

    A voice message carries no words in the chat (its transcript is the
    archivist's), so the card's copy stays."""
    if message.kind == "voice":
        return
    held = originals(sources)
    if not held and message.quote:
        sources.insert(0, message)
        return
    same = next((s for s in held if s.id == message.id), None)
    if same is not None and same.reconstructed:
        sources[sources.index(same)] = _rebuilt(same, message)


def _rebuilt(card: Source, chat: Source) -> Source:
    """The original as the chat has it, with what only the card keeps of
    it: the embed or link of its file, its hash and its text form."""
    media_line = card.quote.strip().split("\n", 1)[0] if card.media_link else ""
    quote = "\n\n".join(p for p in (media_line, chat.quote) if p)
    return replace(chat, kind=card.kind if card.media_link else chat.kind, quote=quote,
                   media=card.media, text=dict(card.text))


def _add(sources: list[Source], source: Source) -> None:
    """Append `source` unless the note holds its id. A reply the card keeps
    only as a reconstruction (no id, no time) is replaced by the real one
    with the same words, which is how the migration's copy gets its id."""
    if not source.quote or source.person.endswith("-bot"):
        return
    if source.id != UNKNOWN and source.id in {s.id for s in sources}:
        return
    copy = next((s for s in sources if s.reconstructed and s.role == source.role
                 and (s.person.casefold(), s.words) == (source.person.casefold(), source.words)), None)
    if copy is not None:
        sources[sources.index(copy)] = source
    else:
        sources.append(source)


def _shows_a_summary(card_text: str) -> bool:
    """Whether the card shows a summary callout where a clean version would be."""
    return interpretation_of(card_text).lstrip().startswith(SUMMARY_CALLOUT)


def _rebuild_of(first: Source, sources: Sequence[Source], history: CardHistory | None,
                card: dict, shown: str = "") -> Rebuild | None:
    """What a note with a clean version is rebuilt from.

    None when nobody corrected or edited the note. The note is then filed
    again from its words alone.

    The events are every correction in the sources and every edit of the
    clean version in the history. The edits start from the clean version
    before the first edit. Without a history there are no edits, and only
    the corrections apply.

    The clean version is "" for a note that shows none, and the reader
    then makes one first. The fields and ``shown`` are the card's values
    as the card holds them now. ``shown`` is the clean version.
    """
    said = [Correction(s.person, s.time, s.words) for s in sources
            if s.role in (REPLY, NOTE) and s.words]
    prior, edits = (history.prior, history.edits) if history else ("", ())
    events = in_order([*said, *edits])
    if not events:
        return None
    start = "" if prior.lstrip().startswith(">") else prior.strip()
    fields = {key: card[key] for key in ("title", "tags", "persons", "date") if key in card}
    current = "" if shown.lstrip().startswith(">") else shown.strip()
    return Rebuild(name_of(first.person), start, tuple(events), fields, current)


def _filer(sidecar: dict, first: Source | None) -> str:
    """Who filed the note: its sidecar says so, else its first original's sender."""
    person = str(sidecar.get("filed_by") or (first.person if first else ""))
    return "" if person == UNKNOWN else person


def _persons(fields: dict) -> list[str]:
    """The people the card is about, as its frontmatter names them."""
    given = fields.get("persons") or []
    return [str(p) for p in given if isinstance(p, str) and p.strip()] if isinstance(given, list) else []
