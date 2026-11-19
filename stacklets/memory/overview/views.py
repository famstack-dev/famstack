"""The views of the memory's pages: which sections each page gets, from which cards.

    overview(graph, "me", audience=frozenset({"marge"}), today=date(2026, 10, 1))
    overview(graph, "topic/topic-camping", audience=..., today=...)
    overview(graph, "nonsense", ...)       # raises NoSuchPage

A view is the address of a page: its kind (`me`, `family`, `person`,
`topic`, `sender`, `documents`, `kind`, `senders`, `everything`) and, for
some, what it is about (`person/bart`, `kind/note`). Each kind is one function here that picks the page's
cards and calls the sections its page shows (`sections.py`), the links and
counts (`tally.py`). The table of views and their sections is in the
package's docstring, which is the contract the pages read.

A page that cannot be shown raises `NoSuchPage`, whatever the reason: no
such person, or one the audience may not read, which the graph does not
hold either. The two look the same, so a 404 says nothing about what
another person keeps.

**A long list comes in parts.** The pages with a long list of cards
(`person/<id>`, `topic/<id>`, `sender/<id>`, `kind/<type>`,
`documents[/<kind>]`) answer it 40 cards at a time, newest first: `part`
picks which (1 is the newest), and the list adds `part` and `parts` to
its `count` and `items`. The page's other sections stay whole on every
part. Each part is cut from the same filtered graph as the page, so a
part holds nothing the page could not. A part before the first or after
the last, and a part of a page that shows a few of many things (`me`,
`family`), is no page: each part has one address, and no address shows
the same cards twice.

    overview(graph, "kind/note", audience=..., today=..., part=2)
    # {"cards": {"count": 45, "items": [the 5 oldest], "part": 2, "parts": 2}, ...}
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from math import ceil

from ..graph import Card, Graph
from .items import item, listed
from .scope import (KINDS, Person, document_kind, entries, in_topic, is_about, own_folder, person_of,
                    sender_of, sent_by, topic_of)
from .sections import (changed, documents, lists_of, open_lists, people, senders, topics,
                       up_next)
from .tally import counts, links

__all__ = ["NoSuchPage", "overview"]

# How many items a part of a page lists: a page about many things shows a
# few of each; a page of one thing (a topic, a sender, the documents) lists more.
SHORT = 8
LONG = 40


class NoSuchPage(LookupError):
    """The view names no page the audience may see: the API answers 404."""


def overview(graph: Graph, view: str, *, audience: frozenset[str] | None, today: date,
             part: int = 1) -> dict:
    """The sections of the page `view` names, from the cards of `graph`.

    `graph` holds only what `audience` may read (`load_graph(audience=...)`);
    `audience` says who "me" is and must then be one person. `today` is the
    household's day, from which "up next" looks ahead and "what changed"
    looks back. Returns `{"view": view, "today": "2026-10-01", "total": 12,
    <sections>}`
    as the package's docstring describes; raises `NoSuchPage` for a view
    that is no page, or names something the graph does not hold. `part`
    picks the part of a page of one list; any part but 1 of another page,
    and a part past the list, raises `NoSuchPage` too.

    Example: `overview(graph, "person/bart", ...)` for Marge answers Bart's
    person, up next, lists, topics, what changed, documents, cards, links
    and counts.
    """
    kind, _, about = view.strip("/").partition("/")
    if kind in _LISTS:
        sections = _LISTS[kind](graph, about, audience, today, part)
    elif part != 1 or kind not in _VIEWS:
        raise NoSuchPage(f"no page {view!r} part {part}")
    else:
        sections = _VIEWS[kind](graph, about, audience, today)
    return {"view": view, "today": today.isoformat(), "total": sum(sections["counts"].values()),
            **sections}


# ── The pages ─────────────────────────────────────────────────────────────

def _me(graph: Graph, about: str, audience: frozenset[str] | None, today: date) -> dict:
    """The page of the one person of the audience: "My memory"."""
    if about or audience is None or len(audience) != 1:
        raise NoSuchPage("'me' is the page of exactly one person")
    [slug] = audience
    person = person_of(graph, slug) or Person(slug)
    everything = entries(graph)
    theirs = [c for c in everything if is_about(c, person)]
    return {"person": person.as_json(),
            **_personal(graph, person, theirs, everything, today),
            "topics": topics(graph, everything, person),
            "bookmarks": listed([c for c in theirs if c.type == "bookmark"], SHORT),
            "senders": senders(graph, theirs)}


def _person(graph: Graph, about: str, audience: frozenset[str] | None, today: date, part: int) -> dict:
    """The page of one person, as the audience may see it, with part
    `part` of the cards about them."""
    person = person_of(graph, about) if about else None
    if person is None:
        raise NoSuchPage(f"no person {about!r}")
    everything = entries(graph)
    theirs = [c for c in everything if is_about(c, person)]
    return {"person": person.as_json(), **_personal(graph, person, theirs, everything, today),
            "topics": topics(graph, everything, person),
            "cards": _part(theirs, part)}


def _personal(graph: Graph, person: Person, theirs: list[Card], everything: list[Card],
              today: date) -> dict:
    """What "My memory" and a person's page share: their up next, lists,
    what changed, documents, links and counts.

    The lists tile counts what the lists section shows: their lists by the
    lists' own rule (`ListCard.is_theirs`), with an open item. The
    groceries name no one and are everyone's; a list with every item
    ticked is in neither, only in the menu's count of every list."""
    lists = [lst for lst in lists_of(everything) if lst.is_theirs(person)]
    shown = [lst.card for lst in lists if lst.parsed.open_items]
    counted = [c for c in theirs if c.type != "list"] + shown
    return {"up_next": up_next(graph, theirs, lists, today, person=person),
            "lists": open_lists(lists),
            "changed": changed(theirs, today, limit=SHORT),
            "documents": documents(theirs, limit=SHORT),
            "links": links(graph, theirs, leave_out=person.id),
            "counts": counts(counted)}


def _family(graph: Graph, about: str, audience: frozenset[str] | None, today: date) -> dict:
    """The page of the whole family: everything the audience may read."""
    if about:
        raise NoSuchPage(f"no page 'family/{about}'")
    everything = entries(graph)
    lists = lists_of(everything)
    household = [p for c in graph.cards.values() if c.type == "person"
                 if (p := person_of(graph, c.id)) is not None]
    return {"people": people(household, everything),
            "up_next": up_next(graph, everything, lists, today),
            "lists": open_lists(lists),
            "changed": changed(everything, today, limit=SHORT),
            "documents": documents(everything, limit=SHORT),
            "links": links(graph, everything),
            "counts": counts(everything)}


def _topic(graph: Graph, about: str, audience: frozenset[str] | None, today: date, part: int) -> dict:
    """The page of one topic: its cards, part `part` of them, and what
    comes up in them."""
    topic = topic_of(graph, about) if about else None
    if topic is None:
        raise NoSuchPage(f"no topic {about!r}")
    cards = in_topic(entries(graph), topic)
    return {"topic": {"id": topic.id, "title": topic.title, "owner": own_folder(graph, topic)},
            "up_next": up_next(graph, cards, lists_of(cards), today),
            "cards": _part(cards, part),
            "links": links(graph, cards),
            "counts": counts(cards)}


def _sender(graph: Graph, about: str, audience: frozenset[str] | None, today: date, part: int) -> dict:
    """The page of one sender: the documents from them, part `part`."""
    sender = sender_of(graph, about) if about else None
    if sender is None:
        raise NoSuchPage(f"no sender {about!r}")
    docs = sent_by(entries(graph), sender)
    kinds = sorted({document_kind(d) for d in docs} - {""})
    return {"sender": {"id": sender.id, "title": sender.title, "kinds": kinds},
            "cards": _part(docs, part),
            "links": links(graph, docs),
            "counts": counts(docs)}


def _documents(graph: Graph, kind: str, audience: frozenset[str] | None, today: date, part: int) -> dict:
    """The documents page, of one kind (`documents/Receipt`) or of all,
    part `part` of their list. A kind the audience has no document of is
    no page, as an unknown topic is not. The kinds' tiles count every
    document on every part."""
    docs = [c for c in entries(graph) if c.type == "document"]
    shown = [d for d in docs if document_kind(d) == kind] if kind else docs
    if kind and not shown:
        raise NoSuchPage(f"no documents of the kind {kind!r}")
    return {"documents": {**documents(docs, kind=kind, limit=0), **_part(shown, part)},
            "senders": senders(graph, docs),
            "links": links(graph, shown),
            "counts": counts(shown)}


def _kind(graph: Graph, card_type: str, audience: frozenset[str] | None, today: date, part: int) -> dict:
    """The page of one kind of card (`kind/note`): its cards, newest first,
    part `part` of them.
    A kind the audience has no card of is no page, as a document kind
    with no document is not."""
    cards = [c for c in entries(graph) if c.type == card_type] if card_type in KINDS else []
    if not cards:
        raise NoSuchPage(f"no cards of the kind {card_type!r}")
    return {"cards": _part(cards, part), "links": links(graph, cards), "counts": counts(cards)}


def _senders(graph: Graph, about: str, audience: frozenset[str] | None, today: date) -> dict:
    """The page of every sender: each with how many documents it sent.
    Only a sender of a document the audience may read is there, so a
    sender card that sent only another person's own papers is not."""
    if about:
        raise NoSuchPage(f"no page 'senders/{about}'")
    docs = [c for c in entries(graph) if c.type == "document" and c.sender_card]
    return {"senders": senders(graph, docs), "counts": counts(docs)}


def _everything(graph: Graph, about: str, audience: frozenset[str] | None, today: date) -> dict:
    """The menu's "Everything": each kind of card the audience has cards
    of, in the order of `KINDS`, with its count, and how many senders
    sent it documents. The counts are those the kinds' pages answer."""
    if about:
        raise NoSuchPage(f"no page 'everything/{about}'")
    everything = entries(graph)
    tally = counts(everything)
    return {"kinds": [{"type": t, "count": tally[t]} for t in KINDS if tally.get(t)],
            "senders": {"count": len(senders(graph, everything))},
            "counts": tally}


# ── A part of a list ──────────────────────────────────────────────────────

def _part(cards: list[Card], part: int) -> dict:
    """Part `part` of `cards` (newest first) as a part that lists items:
    `count` all of them, `items` the `LONG` of this part, `part` and
    `parts`, how many there are. An empty list is one empty part, so its
    page still opens; raises `NoSuchPage` for a part outside 1..parts.

        _part(45 cards, 2)   # {"count": 45, "items": [5 items], "part": 2, "parts": 2}
    """
    parts = max(1, ceil(len(cards) / LONG))
    if not 1 <= part <= parts:
        raise NoSuchPage(f"no part {part} of {parts}")
    start = (part - 1) * LONG
    return {"count": len(cards), "items": [item(c) for c in cards[start:start + LONG]],
            "part": part, "parts": parts}


# The pages of a few of many things, and the pages with a long list of
# cards, whose list comes in parts.
_VIEWS: dict[str, Callable[[Graph, str, frozenset[str] | None, date], dict]] = {
    "me": _me, "family": _family, "senders": _senders, "everything": _everything,
}
_LISTS: dict[str, Callable[[Graph, str, frozenset[str] | None, date, int], dict]] = {
    "person": _person, "topic": _topic, "sender": _sender, "documents": _documents, "kind": _kind,
}
