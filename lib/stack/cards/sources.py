"""A card's sources and its sidecar: what the card was made from, and where it came from.

ADR-018. A card that is made from things people sent keeps what a reader
reads: its fields, the interpretation (the clean version, or a summary),
then `## Sources`, one blockquote per thing it was made from, in order.
The sidecar `<stem>.json` beside it keeps where each came from and how
the card was made. Quote n belongs to `sources[n]`.

    family/itchy-scratchy-land/notes/2026/10/trip-7903f2.md
    family/itchy-scratchy-land/notes/2026/10/trip-7903f2.json

    {"card_id": "note-967b020f7e", "filed_by": "marge",
     "sources": [{"role": "original", "person": "marge", "time": "2026-10-21T08:55:00+00:00",
                  "channel": "chat", "kind": "text", "id": "$Jn4q…"}],
     "made": {"at": "…", "model": "…", "code_commit": "50efe696",
              "prompt_template": "7c2e91a4", "requested_by": "marge"}}

`made.correction_template` joins `prompt_template` when the note was
refiled with corrections: the hash of the prompt that worked them in.
`made.conversation_template` takes its place when a person had edited the
note by hand, and the card conversation rebuilt it (ADR-018 §7).

A refile reads the sources, never the summary (`sources_of`, `words_of`,
`corrections_of`), and a card that shows a note's sources shows the same
list. This module holds the part every card type reads alike. What reads
a note in the format before this one, and the replies the chat holds, is
the memory stacklet's (`stacklets/memory/note_card.py`).

Stdlib only: the host CLI, the bot runner and the cards API import it.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace

from .lookup import SIDECAR_SUFFIX

SOURCES_HEADING = "## Sources"
ORIGINAL, REPLY, NOTE = "original", "reply", "note"
UNKNOWN = "unknown"

# The fields that say where a record came from and how it was made. They
# live in the sidecar, not in the frontmatter people read (ADR-018 §3).
MACHINE_FIELDS = frozenset({
    "capture_id", "paperless_id", "paperless_url", "entry_id", "event_ids", "timestamp",
    "model", "resource", "media", "digest", "filed_by", "duration_ms", "mode",
    "date_basis", "processing", "source",
})

_MEDIA_LINE = re.compile(r"^(?:!\[\[(/media/[^\]]+)\]\]|\[[^\]]*\]\((/media/[^)]+)\))$")
_HEADING2 = re.compile(r"^##\s+\S")


# ── A source ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Source:
    """One thing a note was made from: its sidecar record and its quote.

    `role` is `original`, `reply` or `note` (what was given to a refile).
    `person` a person's id, `time` when it was sent (ISO 8601 with offset,
    `unknown` when nobody recorded it), `channel` where (`chat`, `cli`, …),
    `kind` what (`text`, `voice`, `image`, `file`), `id` the channel's id
    for it. `media` is `sha256:<hex>` of a binary, `text` which text form
    the quote shows. `quote` is what the card shows, unquoted: a binary's
    embed or link first, then its text.

        Source(ORIGINAL, "marge", "2026-10-21T08:55:00+00:00", "chat", "text", "$Jn4q…",
               quote="lisas sax lesson moved to thu 4pm")
    """

    role: str
    person: str
    time: str
    channel: str
    kind: str
    id: str
    quote: str = ""
    media: str = ""
    text: dict = field(default_factory=dict)
    reconstructed: bool = False

    @property
    def words(self) -> str:
        """The quote's text, without the embed or link of its binary."""
        lines = self.quote.strip().split("\n")
        if lines and _MEDIA_LINE.match(lines[0].strip()):
            lines = lines[1:]
        return "\n".join(lines).strip()

    @property
    def media_link(self) -> str:
        """The `/media/…` path the quote embeds or links, "" for none."""
        first = self.quote.strip().split("\n", 1)[0].strip()
        found = _MEDIA_LINE.match(first)
        return (found.group(1) or found.group(2)) if found else ""

    def record(self) -> dict:
        """The sidecar entry: everything but the quote."""
        entry: dict[str, object] = {"role": self.role, "person": self.person,
                                    "time": self.time, "channel": self.channel,
                                    "kind": self.kind, "id": self.id}
        if self.media:
            entry["media"] = self.media
        if self.text:
            entry["text"] = dict(self.text)
        if self.reconstructed:
            entry["reconstructed"] = True
        return entry

    def shown(self) -> dict:
        """The entry with its quote, as a surface shows a source."""
        return {**self.record(), "quote": self.quote}


def source_from(record: dict, quote: str) -> Source:
    def text(key: str) -> str:
        return str(record.get(key) or UNKNOWN)
    return Source(text("role"), text("person"), text("time"), text("channel"), text("kind"),
                  text("id"), quote=quote, media=str(record.get("media") or ""),
                  text=dict(record.get("text") or {}),
                  reconstructed=bool(record.get("reconstructed")))


def new_id(channel: str) -> str:
    """An id for a source whose channel has none of its own (`cli`, `agent`, `cards`)."""
    return f"{channel}:{secrets.token_hex(8)}"


def now() -> str:
    """This moment, as a source's or a card's time."""
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def time_of_ms(ms) -> str:
    """A Matrix `origin_server_ts` (milliseconds) as a source's time; "" for none."""
    if not isinstance(ms, (int, float)) or ms <= 0:
        return ""
    return _dt.datetime.fromtimestamp(ms / 1000, _dt.timezone.utc).isoformat(timespec="seconds")


# ── The card ──────────────────────────────────────────────────────────

def sidecar_path(card_path: str) -> str:
    """`family/notes/2026/10/trip-7903f2.md` → `family/notes/2026/10/trip-7903f2.json`."""
    return card_path.removesuffix(".md") + SIDECAR_SUFFIX


def card_fields(fields: dict) -> dict:
    """`fields` without the machine fields, in their order."""
    return {k: v for k, v in fields.items() if k not in MACHINE_FIELDS}


def render_sources(sources: Sequence[Source]) -> str:
    """The `## Sources` section: one blockquote per source, in order."""
    quotes = ["\n".join(f"> {ln}" if ln.strip() else ">" for ln in s.quote.strip().split("\n"))
              for s in sources]
    return "\n\n".join([SOURCES_HEADING, *quotes])


def quotes_of(card_text: str) -> list[str]:
    """The quotes under `## Sources`, unquoted, in order; [] for a card without it."""
    lines = card_text.split("\n")
    try:
        start = lines.index(SOURCES_HEADING) + 1
    except ValueError:
        return []
    quotes: list[list[str]] = []
    inside = False
    for line in lines[start:]:
        if _HEADING2.match(line):
            break
        if line.startswith(">"):
            if not inside:
                quotes.append([])
            quotes[-1].append(line[2:] if line.startswith("> ") else line[1:])
            inside = True
        else:
            inside = False
    return ["\n".join(q).strip() for q in quotes]


def has_sources(card_text: str) -> bool:
    return SOURCES_HEADING in card_text.split("\n")


def interpretation_of(card_text: str) -> str:
    """What a note's card says it is about, without its title, meta block
    and sources: the clean version, or the briefing callout as written."""
    lines = card_text.split("\n")
    if card_text.startswith("---\n") and "---" in lines[1:]:
        lines = lines[lines.index("---", 1) + 1:]
    kept, started = [], False
    for line in lines:
        if line == SOURCES_HEADING:
            break
        if not started and (not line.strip() or line.startswith("# ") or line.startswith("> **")):
            continue
        started = True
        kept.append(line)
    return "\n".join(kept).strip()


# ── The sidecar ───────────────────────────────────────────────────────

def sidecar_text(*, card_id: str, filed_by: str, sources: Sequence[Source], made: dict) -> str:
    """The sidecar of a note, as written: two-space JSON, one trailing newline."""
    return json.dumps({"card_id": card_id, "filed_by": filed_by,
                       "sources": [s.record() for s in sources],
                       "made": {k: v for k, v in made.items() if v}},
                      ensure_ascii=False, indent=2) + "\n"


def read_sidecar(text: str | None) -> dict:
    """A sidecar's content; {} for none or one that does not parse."""
    try:
        loaded = json.loads(text or "")
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def sidecar_of(card_file) -> dict:
    """The sidecar beside the card file `card_file` (a `Path`), {} for none."""
    try:
        return read_sidecar(card_file.with_suffix(SIDECAR_SUFFIX).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return {}


class SourcesMismatch(ValueError):
    """A card whose quotes and sidecar sources do not pair up."""


def sources_of(card_text: str, sidecar: dict) -> list[Source]:
    """The note's sources: each sidecar record with its quote.

    Raises `SourcesMismatch` when the counts differ: which quote is whose
    is then a guess, and a refile must not make it.
    """
    records = [r for r in sidecar.get("sources") or [] if isinstance(r, dict)]
    quotes = quotes_of(card_text)
    if len(records) != len(quotes):
        raise SourcesMismatch(f"{len(quotes)} quote(s) under {SOURCES_HEADING} and "
                              f"{len(records)} source(s) in the sidecar")
    return [source_from(r, q) for r, q in zip(records, quotes)]


# ── What a refile reads ───────────────────────────────────────────────
#
# Sources are only appended (ADR-018 §4). A person's hand edit of a quote
# is undone and kept as their `note` (`with_quote_edits`); the model is
# given the original's words and the corrections after it.

def with_quote_edits(sources: Sequence[Source], quotes_as_sent: Iterable[tuple[str, str]],
                     quote_edits: Sequence[Source]) -> list[Source]:
    """`sources` with each quote a person edited by hand put back, and their edits appended.

    A quote holds the words as sent (ADR-018 §4), so a hand edit of one
    is the person's correction, not new words of the sender's.
    `quotes_as_sent` pairs a source id with the quote to put back;
    `quote_edits` are the edits as `note` sources (`stack.cards.history`). An
    edit is appended unless a note by the same person with the same words
    is there already: a refile that was applied holds it, and the card's
    history still shows the edit to every later refile.

        with_quote_edits(sources, (("$cast:simpson", "haybart the cast comes off"),),
                         (Source(NOTE, "marge", "…", "cards", "text", "cards:…",
                                 quote="Hey Bart, the cast comes off"),))
    """
    as_sent = dict(quotes_as_sent)
    kept = [replace(s, quote=as_sent[s.id]) if s.id in as_sent else s for s in sources]
    for edit in quote_edits:
        if not any(s.role == NOTE and (s.person, s.words) == (edit.person, edit.words) for s in kept):
            kept.append(edit)
    return kept


def words_of(sources: Sequence[Source]) -> str:
    """The words of the note's original, "" when it has none."""
    return next((s.words for s in sources if s.role == ORIGINAL), "")


def corrections_of(sources: Sequence[Source]) -> str:
    """Every reply and note, one per line, after the name of who wrote it."""
    return "\n".join(f"{_name(s.person)}: {s.words}" for s in sources
                     if s.role in (REPLY, NOTE) and s.words)


def _name(person: str) -> str:
    return person[:1].upper() + person[1:] if person.islower() else person
