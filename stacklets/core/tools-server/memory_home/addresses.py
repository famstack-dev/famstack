"""Every address of the memory's pages, built in one place.

The memory's pages link to each other as a graph: "My memory" and the family's page name people, topics, senders and
kinds of document, and each of those has a page of its own. This module is the one place the pages' Python builds those
addresses, from the two builders of `memory_ui.addresses`, which spells a card's own page too: a card's page links into
the memory, and the card pages cannot import this package. Two places spell the same paths by hand, because they cannot
call this module: the routes in `server.py`, and the preview's script (`static/preview.js`), which asks
`/memory/preview/<card id>` for a link to `/card/<card id>`.

    home()                                   '/memory'
    family()                                 '/memory/family'
    person("bart")                           '/memory/person/bart'
    topic("topic-camping")                   '/memory/topic/topic-camping'
    sender("correspondent-kwik-e-mart")      '/memory/sender/correspondent-kwik-e-mart'
    documents("Receipt")                     '/memory/documents/Receipt'
    kind("note")                             '/memory/kind/note'
    kind("document")                         '/memory/documents'
    senders()                                '/memory/senders'
    search()                                 '/memory/search'
    search_results()                         '/memory/search/results'
    preview("note-7e1a2b3c4d")               '/memory/preview/note-7e1a2b3c4d'
    asset("home.css")                        '/memory/static/home.css'

The person, topic, sender, documents, kind, senders and search pages each have a route
(`routes.py`); every page links the graph through these functions.

Every part is quoted, so an id or a kind with a space or an umlaut
("Kündigung") gives an address a browser sends back unchanged.

Pure: strings in, strings out.
"""

from __future__ import annotations

from memory_ui.addresses import memory_page, memory_search

__all__ = ["home", "family", "person", "topic", "sender", "documents", "kind", "senders", "search",
           "search_results", "preview", "asset"]


def home() -> str:
    """"My memory": the page of the person on the device."""
    return memory_page()


def family() -> str:
    """The family's page."""
    return memory_page("family")


def person(person_id: str) -> str:
    """The memory page of the person `person_id` (`bart`)."""
    return memory_page("person", person_id)


def topic(card_id: str) -> str:
    """The memory page of the topic with the card id `card_id`."""
    return memory_page("topic", card_id)


def sender(card_id: str) -> str:
    """The memory page of the sender with the card id `card_id`."""
    return memory_page("sender", card_id)


def documents(kind: str = "") -> str:
    """The documents page, or the page of one kind of document (`Receipt`)."""
    return memory_page("documents", kind)


def kind(card_type: str) -> str:
    """The page of one kind of card (`note`, `diary`). The documents keep
    their own page, which shows them by kind of document, so `document`
    leads there."""
    return documents() if card_type == "document" else memory_page("kind", card_type)


def senders() -> str:
    """The page of every sender, with how many documents each sent."""
    return memory_page("senders")


def search() -> str:
    """Where the header's search sends its words (`?q=`)."""
    return memory_search()


def search_results() -> str:
    """Where the search's layer reads its results (`?q=`): the search
    page's sheet without the page around it (`pages.open_search_results`)."""
    return memory_page("search", "results")


def preview(card_id: str) -> str:
    """Where a card link's preview is read, as JSON (`pages.open_preview`)."""
    return memory_page("preview", card_id)


def asset(name: str) -> str:
    """The address of one of the pages' own files (`static/`)."""
    return memory_page("static", name)
