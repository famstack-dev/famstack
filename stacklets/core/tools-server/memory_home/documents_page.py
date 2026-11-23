"""The documents page: the family's documents by kind, newest first.

"All documents" and the documents chips of the other pages open
`/memory/documents` and `/memory/documents/<kind>`, drawn from the memory's
overview of the documents (`/overview/documents[/<kind>]`):

    title, sheet = documents_page.sheet(answer, context, kind="Receipt")

The page opens with one tile per kind of document that has documents,
most first, the count first, then the kind and the day of its newest
document (no title, so the tiles stay small); a
tile "All documents" comes before them. The tile of the kind shown is the
current page. Then the newest documents, each naming its kind, 40 at a
time with "Newer" and "Older" between the parts (`paging`), and the
senders, people and topics they name.

The memory narrows the newest documents to the kind and keeps every kind's
tile (`documents.kinds` stays whole), so the page always offers the
others. The "All documents" tile shows the memory's count of every
document (`documents.total`); a document without a kind has no tile of its
own and is only under "All".

Pure: no I/O.
"""

from __future__ import annotations

from html import escape

from memory_home import addresses, sections
from memory_home.frame import page_head
from memory_home.items import Context, type_icon
from memory_home.paging import pager
from memory_home.words import counted, words

__all__ = ["sheet"]


def sheet(answer: dict, context: Context, *, kind: str = "") -> tuple[str, str]:
    """(the browser tab's title, the page's sheet) for the overview
    `answer` of the documents, of one `kind` ("Receipt") or of all ("")."""
    w = words(context.language)
    part = answer.get("documents") or {}
    title = kind or w["documents"]
    facts = escape(counted(part.get("count", 0), w["document_count"]))
    if kind:
        facts += f' · <a href="{escape(addresses.documents())}">{escape(w["all_documents"])}</a>'
    head = page_head("document", w["documents"] if kind else w["everything"], title,
                     f'<span class="count-line">{facts}</span>', kind=kind)
    newest = sections.section("documents", context, sections.listed(part, context),
                              part.get("count") or None, key="newest")
    body = newest + pager(part, addresses.documents(kind), context) + sections.links(answer, context)
    return title, (f'<article class="sheet home">{head}{_tiles(part, context, kind)}'
                   f'<div class="sections">{body}</div></article>')


# ── Parts ────────────────────────────────────────────────────────────────

def _tiles(part: dict, context: Context, chosen: str) -> str:
    """"All documents" and one tile per kind with documents, most first,
    from the answer's `documents` part; the tile of the page shown carries
    `aria-current`. "" with none."""
    kinds = part.get("kinds", [])
    named = [k for k in kinds if k.get("kind") and k.get("count")]
    if not kinds:
        return ""
    w = words(context.language)
    newest = max((k["newest"] for k in kinds if k.get("newest")), key=lambda i: i.get("day", ""),
                 default=None)
    tiles = [_tile(addresses.documents(), w["all_documents"], "", part.get("total", 0),
                   newest, context, current=not chosen)]
    tiles += [_tile(addresses.documents(k["kind"]), k["kind"], k["kind"], k["count"], k.get("newest"),
                    context, current=k["kind"] == chosen) for k in named]
    return (f'<nav class="kindcards" aria-label="{escape(w["kinds_label"])}">'
            f'{"".join(tiles)}</nav>')


def _tile(href: str, label: str, kind: str, count: int, newest: dict | None, context: Context, *,
          current: bool) -> str:
    """One tile: the count first, then the kind, then the day of its
    newest document. The title is left out because it made every tile
    two lines taller; the "Newest" list under the tiles names the documents."""
    w = words(context.language)
    when = ""
    if newest:
        day = context.day_words(newest.get("day"))
        when = w["newest_on"].format(day=day) if day else ""
    mark = ' aria-current="page"' if current else ""
    return (f'<a class="kindcard" data-type="document" href="{escape(href)}"{mark}>'
            f'<span class="kc-top">{type_icon("document", kind=kind, size=16)}'
            f'<span class="kc-n">{count}</span></span>'
            f'<span class="kc-label">{escape(label)}</span>'
            + (f'<span class="kc-day">{escape(when)}</span>' if when else "") + "</a>")
