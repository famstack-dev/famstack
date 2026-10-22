"""A note card: the old format it is read from, and the replies the chat holds.

ADR-018. A note keeps its sources under `## Sources` and in its sidecar
(`stack.cards.sources`, the part every card reads alike). This module
holds what is the memory's own about a note:

- a note filed before that format is read the way migration memory/0008
  converts it (`legacy_note`), so both shapes refile alike
  (`card_sources`);
- a refile reads the note's sources with what the chat holds that they
  do not: replies in the message's thread nobody filed, handed in as
  Matrix events (`note_sources`).

The memory stacklet owns the format; the archivist writes it through this
module and `stack.cards.sources`. Stdlib only: the host CLI, the bot
runner and the cards API import it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace

from stack.cards.sources import (
    NOTE,
    ORIGINAL,
    REPLY,
    UNKNOWN,
    Source,
    card_fields,
    has_sources,
    new_id,
    now,
    render_sources,
    sidecar_text,
    sources_of,
    time_of_ms,
    with_quote_edits,
)
from stack.frontmatter import FrontmatterError, dump, parse

# What an older reprocess appended to the words it classified, which a
# refile then kept as the note's words.
PROMPT_RESIDUE = ("Existing tags on this entry:", "The sender's caption:")

# Where a `## ` section ends: at the next one.
_HEADING2 = re.compile(r"^##\s+\S")


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
# replaces a quote. A person's hand edit of a quote is undone and kept as
# their `note` (`with_quote_edits`). A correction given with the refile
# comes last, as a `note`.

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
                 at: str = "", quotes_as_sent: Iterable[tuple[str, str]] = (),
                 quote_edits: Sequence[Source] = ()) -> list[Source]:
    """What a refile of the note reads, in order.

    The card's sources, with a person's hand edits of their quotes undone
    and appended as that person's notes (`with_quote_edits`, from the
    card's history), then each reply in the chat `thread` they do not
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
    sources = with_quote_edits(card_sources(card_text, sidecar), quotes_as_sent, quote_edits)
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
