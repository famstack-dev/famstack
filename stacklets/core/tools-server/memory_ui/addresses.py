"""Every address of the card pages, built in one place.

A card of the family memory has one address for its whole life,
`/card/<card id>`, and every link on its page leads to an address of
this family: another card, a card's history and full text, a stored
file, the pages' stylesheet. This module is the only place that spells
them, from `access.CARD_PREFIX`, which also decides who may read them.

    card_address("person-homer")             '/card/person-homer'
    card_address("homer/notes/grill.md")     '/card/homer/notes/grill.md'
    history_address("note-0b6e2d91aa")       '/card/note-0b6e2d91aa/history'
    text_address("note-4e1a9b7c2d")          '/card/note-4e1a9b7c2d/text'
    media_address("2026/11/Q7uno.pdf")       '/card/api/media/2026/11/Q7uno.pdf'
    asset_address("memory.css")              '/card/static/memory.css'
    paperless_page("http://mac.lan:42020", 72)
                                             'http://mac.lan:42020/documents/72/details'

Why one place: the address was spelled in eight modules once, and the two
builders of a stored file's address quoted differently, so a file whose
name holds an umlaut had one address in its panel and another in the
text. A change to the address (a prefix, quoting a new kind of id) now
touches this file, and a test keeps the other modules from spelling it.

There is no page at `/card` itself: the card pages are reached from a
link in the chat (`/go/card/<card id>`) or from another card, and the
memory's overview with a search is not part of them.

Every part of a path is quoted, so an id or a file name with a space or
an umlaut gives an address a browser sends back unchanged. A card named by
its file's path keeps its slashes: the server sends that address on to the
card's id (`pages.landing`).

One address outside the memory is made here too: a Paperless document's
page, from Paperless's public address of the moment. A document's card
page links it ("Open in Paperless"), and the resolver sends a `/go/docs`
link without a card there. Both import it from this module, which
imports nothing of the pages or the resolver, so neither imports the
other.

Pure: strings in, strings out.
"""

from __future__ import annotations

from urllib.parse import quote

from access import CARD_PREFIX

__all__ = ["card_address", "history_address", "text_address", "media_address",
           "asset_address", "paperless_page"]


def card_address(ref: str) -> str:
    """The address of the card `ref` names.

    `ref` is a card id, or a card's file path when the caller knows only
    that (a link in a card's text, a link found by a Paperless number): the
    path keeps its slashes, and `pages.landing` sends it on to the card's id.

        >>> card_address("person-homer")
        '/card/person-homer'
        >>> card_address("family/documents/2026/10/duff-insurance-policy-p72.md")
        '/card/family/documents/2026/10/duff-insurance-policy-p72.md'
    """
    return f"{CARD_PREFIX}/{_path(ref)}"


def history_address(card_id: str) -> str:
    """Where a card's history is asked for when "History" opens.

        >>> history_address("note-0b6e2d91aa")
        '/card/note-0b6e2d91aa/history'
    """
    return f"{CARD_PREFIX}/{_part(card_id)}/history"


def text_address(card_id: str) -> str:
    """Where a note's full text is asked for when "Full text" opens.

        >>> text_address("note-4e1a9b7c2d")
        '/card/note-4e1a9b7c2d/text'
    """
    return f"{CARD_PREFIX}/{_part(card_id)}/text"


def media_address(path: str) -> str:
    """The address of the media store's file `path` (`2026/11/x.pdf`),
    read through the card pages' read routes.

        >>> media_address("2026/11/Q7uno.pdf")
        '/card/api/media/2026/11/Q7uno.pdf'
        >>> media_address("2026/11/Grüße.jpg")
        '/card/api/media/2026/11/Gr%C3%BC%C3%9Fe.jpg'
    """
    return f"{CARD_PREFIX}/api/media/{_path(path)}"


def asset_address(name: str) -> str:
    """The address of the pages' stylesheet or script.

        >>> asset_address("memory.js")
        '/card/static/memory.js'
    """
    return f"{CARD_PREFIX}/static/{_part(name)}"


def paperless_page(docs_base: str, doc_id: int | str) -> str:
    """The page of Paperless document `doc_id` at `docs_base`, Paperless's
    public address today; "" without one.

    The one place core spells a Paperless document's address. It is made
    when a page is shown or a link is tapped, never stored, so a document
    still opens after Paperless moves to another address.

        >>> paperless_page("http://mac.lan:42020/", 72)
        'http://mac.lan:42020/documents/72/details'
        >>> paperless_page("", 72)
        ''
    """
    if not docs_base:
        return ""
    return f"{docs_base.rstrip('/')}/documents/{doc_id}/details"


# ── Quoting ────────────────────────────────────────────────────────────────

def _path(path: str) -> str:
    """`path` with each of its parts quoted and its slashes kept."""
    return "/".join(_part(p) for p in path.split("/"))


def _part(part: str) -> str:
    return quote(part, safe="")
