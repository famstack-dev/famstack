"""Diary cards in the format before ADR-018, converted to sources by migration 0012.

Before ADR-018 a diary card kept where it came from in its frontmatter
(`entry_id`, `event_ids`, `timestamp`, `filed_by`, `media`, `model`,
`resource`, `mode`, `date_basis`, `duration_ms`), its files as embeds in
its body, its replies under `## Replies` and the family's words last,
under "Transcript", "Text" or "Caption". Migration `memory/0012`
converts each such card on file into the format the diary writes
(`bot/diary/card.py`), without a model call and without reading the
room. The migration is the only user of this module: nothing at run
time reads the old format, and the format check reports an old card
left over as a diary card without its sidecar.

    found, commit = give_diary_cards_their_sources(memory, vault, media_root=root)
    converted = old_card(text, path=path, media_sha=recorded)
    converted.card       # the card: its fields, its words above, `## Sources`
    converted.sidecar    # the sidecar: who sent each message, when, its hash
    converted.problems   # what the old card did not record, one line each

What an old card did not record stays unknown and is marked: a reply's
time is `unknown` and the source `reconstructed`. A card the compile
owned is signed again, so the next compile leaves it as it is. A card a
person changed keeps their words, their fields and the digest it had (or
none), so it stays theirs; it is converted, never rebuilt.

It lives beside the memory's lib and not in `migrations/`, because a
module there runs on the host, and its imports of the card format and
the diary would be host code importing the memory's domain (ADR-019).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from stack.cards import CARD_ID
from stack.cards.sources import (ORIGINAL, REPLY, UNKNOWN, Source, card_fields, has_sources,
                                 render_sources, sidecar_path, sidecar_text, time_of_ms)
from stack.digest import DIGEST, changed_by_hand, signed
from stack.frontmatter import FrontmatterError
from stack.frontmatter import dump as frontmatter_dump
from stack.frontmatter import parse as frontmatter_parse

from stack import media
from stack.memory import MemoryAPI

from .bot.diary.card import DIARY_TYPE
from .bot.diary.sources import files_by_event, quote_of
from .lib import CardLinks, _markdown_files, _read_with_version, commit_found

__all__ = ["Converted", "diary_sources_for", "give_diary_cards_their_sources", "is_old_card",
           "old_card", "old_sources"]

_OLD_WORDS = ("## Transcript", "## Text", "## Caption")
_OLD_REPLIES = "## Replies"
_OLD_REPLY_HEAD = re.compile(r"^\*\*(.+)\*\*$")
_MEDIA_LINE = re.compile(r"^(?:!\[\[(/media/[^\]]+)\]\]|\[[^\]]*\]\((/media/[^)]+)\))\s*$")


@dataclass(frozen=True)
class Converted:
    """A diary card in the format before as the new one: the card, its
    sidecar, and what the old card did not record, one line each."""

    card: str
    sidecar: str
    problems: list[str]


def is_old_card(text: str) -> bool:
    """A diary card written in the format before ADR-018: one that names
    its messages in its frontmatter and has no `## Sources`.

        is_old_card("---\ntype: diary\nevent_ids:\n  - $m1\n---\n\n## Text\n\nHi\n")   # True
        is_old_card("---\ntype: diary\n---\n\n## Sources\n\n> Hi\n")                    # False
    """
    try:
        fields = frontmatter_parse(text)
    except FrontmatterError:
        return False
    return (fields.get("type") == DIARY_TYPE and not has_sources(text)
            and bool(fields.get("event_ids") or fields.get("entry_id")))


def old_card(text: str, *, path: str = "",
             media_sha: Callable[[str], str] = lambda link: "") -> Converted:
    """The old-format diary card `text` in the new format, without a model call.

    Its words, media, title and fields stay as they are, and every other
    line of its body too: only the words, the replies and the embeds move
    into quotes under `## Sources` (`old_sources`). A card the compile
    owns is signed again, so it stays the compile's; a card a person
    changed keeps the digest it had, or none, so it stays theirs.
    `media_sha` gives the hash the media store recorded for a `/media/…`
    link, "" for none.

        old_card(open("2026-03-16-3f9a0c1b2d.md").read(), media_sha=recorded)
        # Converted(card='---\ntype: diary\n…## Sources\n\n> …', sidecar='{…}', problems=[…])
    """
    fields = frontmatter_parse(text)
    owned = not changed_by_hand(text)
    lines = text.partition("\n---\n")[2].split("\n")
    words, lines = _old_words(lines)
    replies, lines = _old_replies(lines)
    lines = _without_embeds_of(lines, _listed(fields, "media"))
    sources = _sources(fields, words, replies, media_sha=media_sha)

    kept = {k: v for k, v in card_fields(fields).items() if k != "prompt_template"}
    new = (f"---\n{frontmatter_dump(kept)}\n---\n\n{_trimmed(lines)}\n\n"
           f"{render_sources(sources)}\n")
    if owned:
        new = signed(new)
    elif stored := fields.get(DIGEST):
        new = _with_digest(new, str(stored))
    sidecar = sidecar_text(
        card_id=str(fields.get(CARD_ID) or ""), filed_by=str(fields.get("filed_by") or ""),
        sources=sources, made={"model": str(fields.get("model") or ""),
                               "prompt_template": str(fields.get("prompt_template") or "")})
    missing = [link for link in _listed(fields, "media") if not media_sha(link)]
    where = path or str(fields.get(CARD_ID) or "diary card")
    return Converted(new, sidecar, _problems(where, replies=len(replies), owned=owned,
                                             missing=missing))


# ── An entry's sources from its old fields ────────────────────────────

# A file's kind, read from its extension, where nothing else recorded it.
_KIND_OF_EXTENSION = {**dict.fromkeys(("m4a", "mp3", "wav", "ogg", "oga", "opus", "aac"), "voice"),
                      **dict.fromkeys(("png", "jpg", "jpeg", "gif", "webp", "heic"), "image"),
                      **dict.fromkeys(("mp4", "mov", "webm"), "video")}


def old_sources(*, event_ids, sender: str, time: str, kind: str, body: str,
                duration_ms: int | None, replies, media,
                media_sha: Callable[[str], str] = lambda link: "") -> list[Source]:
    """The sources of an entry its card knew only by its fields: a list of
    event ids, one block of words, the files, and the replies as who and words.

    No room is read and no model is asked. The ids split in order (a
    compile appended each reply's id after the entry's own):

    - The entry's own messages: all ids but the replies', and at least
      the first. Each is an `original`; the first carries the words, the
      time and the length, the others their files only. The card knows
      how many replies it shows, so counting from the end holds also on
      a card a person changed, where a text line has no file to tell it
      from a reply.
      On a card a person corrected by chat, the correction's id was
      appended after the replies, and the card shows no words for it.
      It then reads as the entry's own message, or, with replies, as the
      last reply's id, each reply taking the id after its own. A text
      line and a correction look the same on an old card (no file, no
      words), and a line under a photo is the more common of the two.
    - The replies' ids follow, one per reply. A reply is a `reply` source
      with its time `unknown`, marked `reconstructed` (ADR-018, "Order").
      A card that shows more replies than it has ids gives the last
      replies the id `unknown`.

    A file named after no id joins the first message. Every source whose
    time or id the card did not record is marked `reconstructed`.

        old_sources(event_ids=["$p1", "$r1"], sender="homer", time="2026-11-10T17:00:04+00:00",
                    kind="image", body="At the lake", duration_ms=None,
                    replies=[("marge", "So cute!")], media=["/media/2026/11/p1.jpg"])
        # [Source('original', 'homer', '2026-11-10T17:00:04+00:00', 'chat', 'image', '$p1',
        #         quote='![[/media/2026/11/p1.jpg]]\nAt the lake'),
        #  Source('reply', 'marge', 'unknown', 'chat', 'text', '$r1', quote='So cute!',
        #         reconstructed=True)]
    """
    ids = list(event_ids) or [UNKNOWN]
    by_event: dict[str, list[str]] = {}
    for event_id, path in files_by_event(ids, media):
        by_event.setdefault(event_id, []).append(path)
    own = max(1, len(ids) - len(replies))
    reply_ids = ids[own:own + len(replies)]
    placed = {p for paths in by_event.values() for p in paths}
    loose = [p for p in media if p not in placed]

    def sha_of(link: str) -> str:
        sha = media_sha(link) if link else ""
        return f"sha256:{sha}" if sha else ""

    sources: list[Source] = []
    for n, event_id in enumerate(ids[:own]):
        files = by_event.get(event_id, []) + (loose if n == 0 else [])
        for place, link in enumerate(files or [""]):
            first = n == 0 and place == 0
            file_kind = (kind if first else _kind_of(link)) if link else (kind if first else "text")
            when = time if first and time else UNKNOWN
            sources.append(Source(
                ORIGINAL, sender or UNKNOWN, when, "chat", file_kind, event_id,
                quote=quote_of(link, file_kind, body if first else ""), media=sha_of(link),
                reconstructed=when == UNKNOWN or event_id == UNKNOWN,
                duration_ms=duration_ms if first else None))
    for n, (who, words) in enumerate(replies):
        event_id = reply_ids[n] if n < len(reply_ids) else UNKNOWN
        link = (by_event.get(event_id) or [""])[0]
        reply_kind = _kind_of(link) if link else "text"
        sources.append(Source(REPLY, who or UNKNOWN, UNKNOWN, "chat", reply_kind, event_id,
                              quote=quote_of(link, reply_kind, words), media=sha_of(link),
                              reconstructed=True))
    return sources


def _kind_of(link: str) -> str:
    return _KIND_OF_EXTENSION.get(link.rsplit(".", 1)[-1].lower(), "file")


# ── Reading the old card ──────────────────────────────────────────────

def _sources(fields: dict, words: str, replies: list[tuple[str, str]], *,
             media_sha: Callable[[str], str]):
    """The card's sources from its old frontmatter, words and replies; the
    first original keeps how the day was found and how the words were spoken."""
    duration = fields.get("duration_ms")
    sources = old_sources(
        event_ids=_listed(fields, "event_ids"), sender=str(fields.get("filed_by") or ""),
        time=_iso_seconds(str(fields.get("timestamp") or "")),
        kind=str(fields.get("kind") or "text"), body=words,
        duration_ms=int(duration) if duration else None, replies=replies,
        media=_listed(fields, "media"), media_sha=media_sha)
    first = next((n for n, s in enumerate(sources) if s.role == ORIGINAL), 0)
    sources[first] = replace(sources[first],
                             date_basis=str(fields.get("date_basis") or "sent"),
                             mode=str(fields.get("mode") or "monologue"))
    return sources


def _problems(where: str, *, replies: int, owned: bool, missing: list[str]) -> list[str]:
    """What the old card at `where` did not record, or what the admin should know, one line each."""
    problems = []
    if replies:
        problems.append(f"{where}: {replies} repl(ies) without a time")
    if not owned:
        problems.append(f"{where}: a person changed it; converted with their words, "
                        "and it stays theirs")
    if missing:
        problems.append(f"{where}: no record in the media store of {', '.join(missing)}")
    return problems


def _listed(fields: dict, key: str) -> list[str]:
    """A frontmatter field that holds one value or a list, as a list of strings."""
    value = fields.get(key) or []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def _old_words(lines: list[str]) -> tuple[str, list[str]]:
    """The family's words (everything under the words heading, which came
    last) and the lines before it."""
    for n, line in enumerate(lines):
        if line.strip() in _OLD_WORDS:
            return "\n".join(lines[n + 1:]).strip("\n").strip(), lines[:n]
    return "", lines


def _old_replies(lines: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """The replies under `## Replies` as who and words, and the lines without them."""
    try:
        start = lines.index(_OLD_REPLIES)
    except ValueError:
        return [], lines
    end = next((n for n in range(start + 1, len(lines)) if lines[n].startswith("## ")), len(lines))
    found: list[tuple[str, list[str]]] = []
    for line in lines[start + 1:end]:
        if author := _OLD_REPLY_HEAD.match(line.strip()):
            found.append((author[1], []))
        elif line.startswith(">") and found:
            found[-1][1].append(line[2:] if line.startswith("> ") else line[1:])
    return ([(who, "\n".join(said).strip()) for who, said in found],
            lines[:start] + lines[end:])


def _without_embeds_of(lines: list[str], media: list[str]) -> list[str]:
    """The lines without the embeds of the card's own `media`, which become
    quotes under `## Sources`. An embed a person added by hand names a file
    the card did not list: it is no source, so it stays where they put it."""
    listed = set(media)

    def own_embed(line: str) -> bool:
        found = _MEDIA_LINE.match(line.strip())
        return found is not None and (found[1] or found[2]) in listed

    return [ln for ln in lines if not own_embed(ln)]


def _trimmed(lines: list[str]) -> str:
    """The lines with runs of blank lines folded to one, no blank edges."""
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _iso_seconds(timestamp: str) -> str:
    """An old card's `timestamp` (`2026-03-16T07:12:03.000Z`) as a source's time; "" for none."""
    if not timestamp:
        return ""
    try:
        moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return time_of_ms(round(moment.timestamp() * 1000))
    except ValueError:
        return ""


def _with_digest(text: str, value: str) -> str:
    """`text` with the digest line `value` last in its frontmatter, as it was."""
    head, sep, rest = text.partition("\n---\n")
    return f"{head}\n{DIGEST}: {value}{sep}{rest}"


# ── The cards on file ─────────────────────────────────────────────────
#
# Migration 0012 converts every old card in the curator's clone in one
# commit by the memory bot (`lib.commit_found`), each on the version it
# read.

def diary_sources_for(vault: Path, *, media_root: Path | None = None) -> CardLinks:
    """What gives the diary cards in the working copy `vault` their sources and a sidecar.

    Each diary card in the format before (where it came from in its
    frontmatter, its words, replies and files in its body) becomes a card
    with `## Sources` and a new sidecar beside it, with the same words,
    files, title and fields. A photo's hash comes from the media store
    under `media_root`. A card the compile owns is signed again; a card a
    person changed keeps its words and stays theirs. What the old card
    did not record (a reply's time) is marked `reconstructed` and listed
    in `left`. A card already converted, and every other card, stay as
    they are.
    """
    def media_sha(link: str) -> str:
        return media.recorded_sha256(media_root, link) if media_root is not None else ""

    found = CardLinks()
    for path, text in sorted(_markdown_files(vault).items()):
        side = sidecar_path(path)
        if not is_old_card(text) or (vault / side).exists():
            continue
        text, found.versions[path] = _read_with_version(vault, path)
        converted = old_card(text, path=path, media_sha=media_sha)
        found.changes[path] = converted.card
        found.changes[side] = converted.sidecar
        found.created.add(side)
        found.left += converted.problems
    return found


def give_diary_cards_their_sources(memory: MemoryAPI, vault: Path, *,
                                   media_root: Path | None = None,
                                   trailer: str = "") -> tuple[CardLinks, str | None]:
    """Give the diary cards in `vault` their sources and a sidecar
    (`diary_sources_for`), in one commit by the memory bot; what the pass
    found and the commit's sha."""
    found = diary_sources_for(vault, media_root=media_root)
    title = f"give {len(found.created)} diary card(s) their sources and a sidecar"
    return found, commit_found(memory, vault, found, title=title, trailer=trailer)
