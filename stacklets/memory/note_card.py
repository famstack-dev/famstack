"""A note card: the sources it keeps, and the old format they are read from.

ADR-018. A note keeps its sources under `## Sources` and in its sidecar
(`stack.cards.sources`, the part every card reads alike). This module
holds what is the memory's own about a note:

- a note filed before that format is read the way migration memory/0008
  converts it (`legacy_note`), so both shapes refile alike
  (`card_sources`). A refile adds what the chat holds and a correction
  to them (`stack.filing.refile.refile_sources`).

The memory stacklet owns the format; the archivist writes it through this
module and `stack.cards.sources`. Stdlib only: the host CLI, the bot
runner and the cards API import it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from stack.cards.sources import (
    ORIGINAL,
    REPLY,
    UNKNOWN,
    Source,
    card_fields,
    has_sources,
    render_sources,
    sidecar_text,
    sources_of,
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
# The note's sources as the card and its sidecar keep them. What the chat
# holds that they do not, and a correction, are added by the refile
# (`stack.filing.refile.refile_sources`).


def card_sources(card_text: str, sidecar: dict | None) -> list[Source]:
    """The sources the card keeps: from its sidecar, or read as the
    migration reads an old-format note; [] for a note without any."""
    if sidecar:
        return sources_of(card_text, sidecar)
    return legacy_note(card_text).sources if is_legacy_note(card_text) else []
