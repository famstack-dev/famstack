"""A note card and its sidecar: what the note is about, and where it came from.

ADR-018. The card keeps what a reader reads: its fields, the
interpretation (the clean version, or a summary), then `## Sources`, one
blockquote per thing the note was made from, in order. The sidecar
`<stem>.json` beside it keeps where each came from and how the card was
made. Quote n belongs to `sources[n]`.

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

A refile reads the sources, never the summary (`note_sources`,
`words_of`, `corrections_of`), and a card that shows a note's sources
shows the same list. A note filed before this format is read the way the
migration converts it (`legacy_note`), so both shapes refile alike.

The memory stacklet owns the format; the archivist writes it through this
module. Stdlib only: the host CLI, the bot runner and the cards API import it.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

from stack.card_lookup import SIDECAR_SUFFIX
from stack.frontmatter import FrontmatterError, dump, parse

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

# What an older reprocess appended to the words it classified, which a
# refile then kept as the note's words.
PROMPT_RESIDUE = ("Existing tags on this entry:", "The sender's caption:")

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


# ── A note in the format before this one ──────────────────────────────
#
# Its words in a "> [!quote]- Original paste" callout, its upload's caption
# under "## Caption", its replies under "## Replies", where it came from in
# the frontmatter. Read the way migration memory/0008 converts it.

PASTE_CALLOUT = "> [!quote]- Original paste"
_FILE_META = re.compile(r"^> \*\*File\*\* \[([^\]]*)\]\((/media/[^)]+)\)")
_DROPPED_META = re.compile(r"^> \*\*(?:Source|File|Captured)\*\* ")
_EMBED = re.compile(r"^!\[\[(/media/[^\]]+)\]\]\s*$")
_AUTHOR = re.compile(r"^\*\*(.+)\*\*$")


@dataclass(frozen=True)
class Legacy:
    """A note in the old format as the new one: card, sidecar, and what was guessed."""

    card: str
    sidecar: str
    sources: list[Source]
    problems: list[str]


def is_legacy_note(card_text: str) -> bool:
    """A note filed in the old format: its words, caption or replies in the old sections."""
    try:
        fields = parse(card_text)
    except FrontmatterError:
        return False
    if fields.get("type") != "note" or has_sources(card_text):
        return False
    lines = card_text.split("\n")
    return bool(fields.get("capture_id")) or PASTE_CALLOUT in lines


def legacy_note(card_text: str, *, path: str = "",
                media_sha: Callable[[str], str] = lambda link: "") -> Legacy:
    """The old-format note `card_text` in the new format, without a model call.

    The words become the `original` source, from `filed_by`, `timestamp`
    and `capture_id`; an upload's embed and caption join it, `kind: image`
    with `media` from `media_sha(link)`. Each reply becomes a `reply`
    source whose time and id nobody recorded, marked reconstructed and
    reported. Words that carry prompt text from an older reprocess are
    kept, marked and reported, as is anything else the old card did not
    record.
    """
    fields = parse(card_text)
    body = _body(card_text)
    words, rest = _callout(body.split("\n"), PASTE_CALLOUT)
    caption_lines, rest = _section(rest, "## Caption")
    caption = "\n".join(caption_lines).strip()
    replies, rest = _section(rest, "## Replies")
    rest, embed, file_link = _without_media(rest)

    where = path or str(fields.get("card_id") or "note")
    problems: list[str] = []
    capture_id = str(fields.get("capture_id") or "")
    filed_by = str(fields.get("filed_by") or "")
    time = str(fields.get("timestamp") or "")
    channel = "chat" if capture_id.startswith("$") else "cli"
    kind, media_line, media = "text", "", ""
    if embed or file_link:
        link = file_link[1] if file_link else embed
        kind = "image" if embed else "file"
        media_line = (f"![[{embed}]]" if embed or file_link is None
                      else f"[{file_link[0]}]({file_link[1]})")
        media = media_sha(link)
        if not media:
            problems.append(f"{where}: no record of {link} in the media store")
    residue = next((r for r in PROMPT_RESIDUE if r in words), "")
    if residue:
        problems.append(f"{where}: its words carry prompt text ({residue!r}), kept and marked")
    missing = [n for n, v in (("filed_by", filed_by), ("timestamp", time), ("capture_id", capture_id))
               if not v]
    if missing:
        problems.append(f"{where}: the card recorded no {', '.join(missing)}")
    quote = "\n\n".join(p for p in (media_line, words, caption) if p)
    sources = [Source(ORIGINAL, filed_by or UNKNOWN, time or UNKNOWN, channel, kind,
                      capture_id or UNKNOWN, quote=quote or "(no words)",
                      media=f"sha256:{media}" if media else "",
                      reconstructed=bool(residue or missing or not quote))]
    kept_replies = [(who, text) for who, text in _replies(replies) if text]
    if kept_replies:
        problems.append(f"{where}: {len(kept_replies)} repl(ies) without a time or an event id")
    sources += [Source(REPLY, who.lower() if " " not in who else who, UNKNOWN, "chat", "text",
                       UNKNOWN, quote=text, reconstructed=True) for who, text in kept_replies]

    made = {"at": time, "model": str(fields.get("model") or ""), "requested_by": filed_by}
    card = (f"---\n{dump(card_fields(fields))}\n---\n\n"
            f"{_trimmed(rest)}\n\n{render_sources(sources)}\n")
    sidecar = sidecar_text(card_id=str(fields.get("card_id") or ""), filed_by=filed_by,
                           sources=sources, made=made)
    return Legacy(card, sidecar, sources, problems)


def _body(text: str) -> str:
    end = text.find("\n---\n", 3)
    return text[end + 5:] if text.startswith("---\n") and end >= 0 else text


def _callout(lines: list[str], opener: str) -> tuple[str, list[str]]:
    """The text of the callout that opens with `opener`, and the lines without it."""
    try:
        start = lines.index(opener)
    except ValueError:
        return "", lines
    end = start + 1
    while end < len(lines) and lines[end].startswith(">"):
        end += 1
    inner = [ln[2:] if ln.startswith("> ") else ln[1:] for ln in lines[start + 1:end]]
    return "\n".join(inner).strip(), lines[:start] + lines[end:]


def _section(lines: list[str], heading: str) -> tuple[list[str], list[str]]:
    """The lines of the `## ` section `heading`, and the lines without it."""
    try:
        start = lines.index(heading)
    except ValueError:
        return [], lines
    end = next((i for i in range(start + 1, len(lines)) if _HEADING2.match(lines[i])), len(lines))
    return lines[start + 1:end], lines[:start] + lines[end:]


def _without_media(lines: list[str]) -> tuple[list[str], str, tuple[str, str] | None]:
    """The lines without the meta lines a field or a source now holds and the
    embed, with the embed's path and the file link's name and path."""
    embed, file_link, kept = "", None, []
    for ln in lines:
        if found := _FILE_META.match(ln):
            file_link = (found.group(1) or "original", found.group(2))
        if _DROPPED_META.match(ln):
            continue
        if (found := _EMBED.match(ln)) and not embed:
            embed = found.group(1)
            continue
        kept.append(ln)
    return kept, embed, file_link


def _replies(lines: list[str]) -> list[tuple[str, str]]:
    replies: list[tuple[str, list[str]]] = []
    for line in lines:
        if author := _AUTHOR.match(line.strip()):
            replies.append((author.group(1), []))
        elif line.startswith(">") and replies:
            replies[-1][1].append(line[2:] if line.startswith("> ") else line[1:])
    return [(who, "\n".join(words).strip()) for who, words in replies]


def _trimmed(lines: list[str]) -> str:
    return re.sub(r"\n{3,}", "\n\n", "\n".join(ln.rstrip() for ln in lines)).strip("\n")


# ── What a refile reads ───────────────────────────────────────────────
#
# The note's sources as the card and its sidecar keep them, with what the
# chat holds that they do not: replies in the message's thread nobody
# filed, read where a Matrix token is (the bot runner) and handed in as
# Matrix events. Sources are only appended (ADR-018 §4), so the chat never
# replaces a quote. A correction given with the refile comes last, as a
# `note`.

# The quote of the replied-to message a client puts above a reply's words.
_REPLY_FALLBACK = re.compile(r"\A(?:>[^\n]*\n)+\n?")


def card_sources(card_text: str, sidecar: dict | None) -> list[Source]:
    """The sources the card keeps: from its sidecar, or read as the
    migration reads an old-format note; [] for a note without any."""
    if sidecar:
        return sources_of(card_text, sidecar)
    return legacy_note(card_text).sources if is_legacy_note(card_text) else []


def note_sources(card_text: str, sidecar: dict | None = None, *, chat: dict | None = None,
                 thread: Sequence[dict] = (), msg: str = "", by: str = "",
                 at: str = "") -> list[Source]:
    """What a refile of the note reads, in order.

    The card's sources, then each reply in the chat `thread` they do not
    hold yet (bots left out), then `msg`, a correction `by` somebody at
    `at`. `chat` is the message the note was sent as.

    Sources are only appended, with one exception: a source the card
    keeps only as a reconstruction (the migration marked it, because the
    old card recorded it badly) is read again from the chat when the chat
    has it. The original then comes from the message, a photo's or a
    file's from its caption, with the card's embed and hash, and a reply
    from the thread, with its event id and time. A voice message carries
    no words in the chat (its transcript is the archivist's), so its copy
    stays.

        sources = note_sources(card, sidecar, thread=events, msg="it is Friday", by="marge")
        words_of(sources), corrections_of(sources)
    """
    sources = card_sources(card_text, sidecar)
    original = next((s for s in sources if s.role == ORIGINAL), None)
    from_chat = _chat_source(chat, ORIGINAL) if chat else None
    if from_chat is not None and from_chat.kind != "voice":
        if original is None and from_chat.quote:
            sources.insert(0, from_chat)
        elif original is not None and original.reconstructed:
            sources[sources.index(original)] = _rebuilt(original, from_chat)
    for event in thread:
        reply = _chat_source(event, REPLY)
        if reply is None or not reply.quote or reply.person.endswith("-bot"):
            continue
        same = next((s for s in sources if s.id == reply.id or (
            s.role == REPLY and (s.person.casefold(), s.words) == (reply.person.casefold(), reply.words))), None)
        if same is None:
            sources.append(reply)
        elif same.reconstructed and same.id != reply.id:
            sources[sources.index(same)] = reply
    if msg.strip():
        sources.append(Source(NOTE, _person(by) or UNKNOWN, at or now(), "cli", "text",
                              new_id("cli"), quote=msg.strip()))
    return sources


def words_of(sources: Sequence[Source]) -> str:
    """The words of the note's original, "" when it has none."""
    return next((s.words for s in sources if s.role == ORIGINAL), "")


def corrections_of(sources: Sequence[Source]) -> str:
    """Every reply and note, one per line, after the name of who wrote it."""
    return "\n".join(f"{_name(s.person)}: {s.words}" for s in sources
                     if s.role in (REPLY, NOTE) and s.words)


# What a chat message sent as a file is, by its `msgtype`.
_ATTACHMENTS = {"m.image": "image", "m.video": "video", "m.audio": "voice", "m.file": "file"}


def _chat_source(event: dict, role: str) -> Source | None:
    """A Matrix message event as a source; None for anything else.

    A file's words are its caption: modern clients send the file's name in
    `filename` and the caption in `body`, and a client with no caption
    sends the name in both or `body` alone (as the archivist reads it when
    it files the upload, `text_utils.attachment_caption`).
    """
    content = event.get("content") or {}
    if event.get("type") != "m.room.message" or not isinstance(content.get("body"), str):
        return None
    kind = _ATTACHMENTS.get(str(content.get("msgtype") or ""), "text")
    text = content["body"]
    if kind != "text":
        filename = content.get("filename")
        text = text if filename and text != filename else ""
    elif role == REPLY:
        text = _REPLY_FALLBACK.sub("", text)
    time = time_of_ms(event.get("origin_server_ts")) or UNKNOWN
    return Source(role, _person(str(event.get("sender") or "")), time, "chat", kind,
                  str(event.get("event_id") or UNKNOWN), quote=text.strip())


def _rebuilt(card: Source, chat: Source) -> Source:
    """The original as the chat has it, with what only the card keeps of
    it: the embed or link of its file, its hash and its text form."""
    media_line = card.quote.strip().split("\n", 1)[0] if card.media_link else ""
    quote = "\n\n".join(p for p in (media_line, chat.quote) if p)
    return replace(chat, kind=card.kind if card.media_link else chat.kind, quote=quote,
                   media=card.media, text=dict(card.text))


def _person(sender: str) -> str:
    """A Matrix id as the person's id: `@marge:simpson` is `marge`."""
    return sender.split(":")[0].lstrip("@").strip().lower()


def _name(person: str) -> str:
    return person[:1].upper() + person[1:] if person.islower() else person
