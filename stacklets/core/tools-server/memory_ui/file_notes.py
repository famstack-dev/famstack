"""A note made from a file: which file, of what kind, and what the note
holds of it.

A PDF, a text file or a photo saved in a room becomes a note. The capture
keeps the file in the memory's media store and puts it at the top of the
note's first source: a file as a link, a photo as an embed. The rest of
that quote is the file's text (a PDF's or a text file's words) or, for a
photo, the sender's caption (docs/memory-format.md, "A note's sources"):

    ## Sources

    > [uno-rules.pdf](/media/2026/11/Q7unoRulesEvent.pdf)
    >
    > Each player gets seven cards. ...

The card's own text above the sources is famstack's summary of the file,
or its description of the photo. Beside the file, the media store keeps a
`.json` with the file's name, type and size; the page reads it to name
the file in its panel and to know the file is there at all.

    found = file_of(card)
    found.path, found.kind                     # ('2026/11/Q7unoRulesEvent.pdf', 'pdf')
    found.text                                 # 'Each player gets seven cards. ...'
    kind_label(found.kind, words("en"))        # 'PDF'
    size_label(240_000, language="en")         # '240 KB'

Why it reads the quote and stores nothing: the card and its sources hold
the full text and the caption already. A PDF's caption, if the sender
wrote one, is the quote's last paragraph and cannot be told from the
file's text, so the full text shows it at its end.

Pure: no I/O. `card` is the cards API's answer to `GET /card/<id>`, with
the sources its sidecar gives.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["FileNote", "file_of", "file_kind", "kind_label", "size_label"]


@dataclass(frozen=True)
class FileNote:
    """The file a note was made from: where it is in the media store, its
    kind ("pdf", "image", "markdown", "text" or "file"), its name as the
    link gives it, and the rest of its source's quote (`text`: the file's
    words, or a photo's caption).

        FileNote("2026/11/Q7unoRulesEvent.pdf", "pdf", "uno-rules.pdf",
                 "Each player gets seven cards.")
    """

    path: str
    kind: str
    name: str
    text: str

    @property
    def is_image(self) -> bool:
        """Whether the note was made from a photo, which the page shows
        itself instead of a panel that opens it."""
        return self.kind == "image"


def file_of(card: Mapping) -> FileNote | None:
    """The file `card` was made from, None for any other card: a note
    whose first source opens with a file of the media store.

        >>> card = {"type": "note", "sources": [{"quote":
        ...     "![[/media/2026/11/B3nch.jpg]]\\n\\nThe one for under the apple tree."}]}
        >>> file_of(card)
        FileNote(path='2026/11/B3nch.jpg', kind='image', name='B3nch.jpg', text='The one for under the apple tree.')
        >>> file_of({"type": "note", "sources": [{"quote": "buy charcoal"}]}) is None
        True
    """
    if card.get("type") != "note":
        return None
    sources = [s for s in card.get("sources") or [] if isinstance(s, Mapping)]
    if not sources:
        return None
    first, _, rest = str(sources[0].get("quote") or "").strip().partition("\n")
    embed, link = _EMBED.fullmatch(first.strip()), _LINK.fullmatch(first.strip())
    target = embed[1] if embed else link[2] if link else ""
    path = target.strip().removeprefix("/").removeprefix("media/")
    if not target.strip().startswith("/media/") or not _SAFE.fullmatch(path) or ".." in path.split("/"):
        return None
    name = link[1] if link else path.rsplit("/", 1)[-1]
    return FileNote(path, _kind_of(path, image=bool(embed)), name, rest.strip())


def file_kind(path: str) -> str:
    """The kind of a file the media store keeps, by its extension: "pdf",
    "markdown", "text", or "file" for any other.

        >>> file_kind("2026/04/PlayProgrammeEvent.pdf"), file_kind("2026/04/x.zip")
        ('pdf', 'file')
    """
    return _kind_of(path, image=False)


def kind_label(kind: str, w: Mapping) -> str:
    """The kind as a reader names it, in the page's words.

        >>> kinds = {"file_kinds": {"pdf": "PDF", "file": "File"}}
        >>> kind_label("pdf", kinds), kind_label("zip", kinds)
        ('PDF', 'File')
    """
    return w["file_kinds"].get(kind, w["file_kinds"]["file"])


def size_label(size: int, *, language: str) -> str:
    """A file's size in the units a reader knows, one decimal below ten.

        >>> size_label(240_000, language="en"), size_label(1_843_201, language="de")
        ('240 KB', '1,8 MB')
        >>> size_label(24, language="en")
        '24 bytes'
    """
    if size < 1000:
        return f"{size} bytes" if language != "de" else f"{size} Bytes"
    for unit, scale in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if size >= scale:
            value = size / scale
            text = f"{value:.1f}" if value < 10 else f"{value:.0f}"
            return f"{text.replace('.', ',') if language == 'de' else text} {unit}"
    return ""


# ── Reading the quote ──────────────────────────────────────────────────────

# A photo's embed and a file's link, as the capture writes them.
_EMBED = re.compile(r"!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
# The characters a path inside the media store has; anything else is not one.
_SAFE = re.compile(r"[\w./-]+")
_KINDS = {"pdf": "pdf", "md": "markdown", "markdown": "markdown", "txt": "text"}


def _kind_of(path: str, *, image: bool) -> str:
    """A file's kind: an embed is a photo, a link's kind its extension."""
    if image:
        return "image"
    return _KINDS.get(path.rsplit(".", 1)[-1].lower(), "file")
