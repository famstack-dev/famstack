"""The sections of the memory's pages, each a pure function of cards and today.

    up_next(dated_cards, their_lists, today)      # {"count": 2, "items": [...]}
    open_lists(their_lists)                       # [{"id": "list-groceries", ..., "open": 3}]
    changed(cards, today, limit=8)                # who changed what in 14 days
    documents(cards, kind="Receipt", limit=40)    # by kind, and the newest
    topics(graph, all_entries, person)            # a person's topics with their counts

`views.py` picks the sections a page shows and the cards each one reads;
the functions here only shape them. None reads a file or runs git: a list
is parsed from the text its card already holds, and what changed comes
from the changes the graph read from the one `git log` it was built with.

Only what the cards hold today is shown. "Up next" fills from a card dated
ahead and from a list item with a due day; on cards without either it is
empty, which is the answer, not a fault.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from stack.lists import FamilyList, Item, read_list

from ..graph import Card, Graph
from .items import item, listed
from .scope import Person, bucket_of, document_kind, in_topic, is_about, own_date

__all__ = ["AHEAD_DAYS", "CHANGED_DAYS", "ListCard", "changed", "documents", "open_lists",
           "people", "senders", "topics", "up_next", "lists_of"]

# How far "Up next" looks ahead, and how far back "What changed" looks.
AHEAD_DAYS = 60
CHANGED_DAYS = 14


@dataclass(frozen=True)
class ListCard:
    """A list card with its parsed items, and the people it names: those its
    items are assigned to and those in its `persons`."""

    card: Card
    parsed: FamilyList
    names: frozenset[str]

    def is_theirs(self, person: Person) -> bool:
        """Whether the list is `person`'s: it sits in their own folder,
        names them or no one (everyone's), or holds an item that is theirs
        (`is_for`). A list naming only Bart is not Marge's; the family's
        calendar, where one date names Homer and the school concert names
        no one, is everyone's, because the concert is."""
        return (bucket_of(self.card) == person.id or not self.names
                or person.id in {p.lower() for p in self.card.persons}
                or any(self.is_for(i, person) for i in self.parsed.items))

    def is_for(self, item: Item, person: Person) -> bool:
        """Whether `item` of this list is `person`'s: assigned to them, or
        to no one. An item that names no one is the list's, so on a list
        whose `persons` name Bart it is Bart's alone."""
        people = [p.lower() for p in (item.people or self.card.persons)]
        return not people or person.id in people


def lists_of(cards: list[Card]) -> list[ListCard]:
    """The list cards among `cards`, parsed. A list that does not parse is left out."""
    found = []
    for card in cards:
        if card.type != "list":
            continue
        try:
            parsed = read_list(card.text, path=card.path)
        except Exception:  # a list file that does not parse shows nowhere, as on its own page
            continue
        named = {p.lower() for i in parsed.items for p in i.people} | {p.lower() for p in card.persons}
        found.append(ListCard(card, parsed, frozenset(named)))
    return found


# ── What comes up, and what is open ───────────────────────────────────────

def up_next(graph: Graph, cards: list[Card], lists: list[ListCard], today: date, *,
            person: Person | None = None, limit: int = 20) -> dict:
    """What comes up in the next `AHEAD_DAYS` days, soonest first.

    The cards of `cards` whose own `date` is after `today`, and the open
    items of `lists` due from `today` on. A card's own date only: the day
    the graph gives an undated card is the day it last changed, which is
    never ahead. With `person`, only the items assigned to them or to no
    one: Bart's balloons on the party list are not Marge's, and the
    school concert on the family's calendar, which names no one, is
    everyone's (`ListCard.is_for`).

    An item comes as its list: `id` and `type` are the list's, `title` the
    item's text, `list` the list's title, `day` the due day. Example: "- [ ]
    Pumpkin 📅 2026-10-05" on the groceries, asked on 2026-10-01, is
    `{"id": "list-groceries", "title": "Pumpkin", "day": "2026-10-05",
    "list": "Groceries", ...}`. An item's people are those it is assigned
    to, each with the page `graph` resolves for them.
    """
    start, end = today.isoformat(), (today + timedelta(days=AHEAD_DAYS)).isoformat()
    ahead = [item(c) for c in cards if start < own_date(c) <= end]
    for lst in lists:
        ahead += [item(lst.card, title=i.text, day=i.due, list=lst.card.title,
                       **_assigned(graph, list(i.people) or list(lst.card.persons)))
                  for i in lst.parsed.open_items
                  if start <= (i.due or "") <= end and (person is None or lst.is_for(i, person))]
    ahead.sort(key=lambda i: (i["day"], i["title"]))
    return {"count": len(ahead), "items": ahead[:limit]}


def open_lists(lists: list[ListCard]) -> list[dict]:
    """Each list with open items, by title: the list as an item, without its
    persons and their pages, plus `open`, how many items are open. Never the items: a long
    list breaks a page, and they are on the list's own page."""
    found = []
    for lst in sorted(lists, key=lambda lst: lst.card.title):
        if n := len(lst.parsed.open_items):
            line = item(lst.card, open=n)
            del line["persons"], line["person_pages"]
            found.append(line)
    return found


# ── What changed ──────────────────────────────────────────────────────────

def changed(cards: list[Card], today: date, *, limit: int = 8) -> dict:
    """The cards of `cards` a person changed in the last `CHANGED_DAYS` days,
    newest first, each with `by` and `on` of that person's newest change.

    A bot's change (a migration, a rebuild in a person's name) does not
    count (`Change.machine`, the rule `/history` reads too): a card only a
    bot touched in the stretch is not there. Newest first by day, then by
    time, so a change without a time sorts by its day alone.
    """
    since, until = (today - timedelta(days=CHANGED_DAYS)).isoformat(), today.isoformat()
    found = []
    for card in cards:
        change = next((c for c in card.changes if since <= c.day <= until and not c.machine), None)
        if change is not None:
            found.append(((change.day, change.at), item(card, by=change.who, on=change.day)))
    found.sort(key=lambda f: f[0], reverse=True)
    return {"count": len(found), "items": [i for _, i in found[:limit]]}


# ── Documents and their senders ───────────────────────────────────────────

def documents(cards: list[Card], *, kind: str = "", limit: int = 8) -> dict:
    """The documents among `cards`: their kinds, each with how many and the
    newest, most first; then `count` and the newest `items`, of `kind` only
    when one is given. `cards` come newest first.

    Example: three receipts and a policy answer `kinds` [Receipt 3, Policy
    1]; with `kind="Policy"`, `count` is 1 and `items` the policy, and
    `total`, the documents of every kind, stays 4.
    """
    docs = [c for c in cards if c.type == "document"]
    by_kind: dict[str, list[Card]] = {}
    for doc in docs:
        by_kind.setdefault(document_kind(doc), []).append(doc)
    kinds = [{"kind": k, "count": len(found), "newest": item(found[0])}
             for k, found in sorted(by_kind.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    shown = [d for d in docs if document_kind(d) == kind] if kind else docs
    return {"kinds": kinds, "total": len(docs), **listed(shown, limit)}


def senders(graph: Graph, cards: list[Card]) -> list[dict]:
    """The senders of the documents among `cards` that have a sender card:
    each with how many documents and their kinds, most documents first."""
    tally: dict[str, list[Card]] = {}
    for card in cards:
        if card.type == "document" and card.sender_card:
            tally.setdefault(card.sender_card, []).append(card)
    ranked = sorted(tally.items(), key=lambda kv: (-len(kv[1]), graph.cards[kv[0]].title))
    return [{"id": sender, "title": graph.cards[sender].title, "count": len(docs),
             "kinds": sorted({document_kind(d) for d in docs} - {""})} for sender, docs in ranked]


# ── Topics and people ─────────────────────────────────────────────────────

def topics(graph: Graph, cards: list[Card], person: Person) -> list[dict]:
    """The topics `person` has cards in, and those in their own folder.

    `cards` are every entry the audience may read, newest first. A topic's
    `cards` counts all of its entries among them, as its own page does;
    `about` those about `person`; `latest` is its newest card. Most `about`
    first, then by title.
    """
    found = []
    for topic in (c for c in graph.cards.values() if c.type == "topic"):
        theirs = in_topic(cards, topic)
        about = sum(1 for c in theirs if is_about(c, person))
        own = bucket_of(topic) == person.id
        if about or own:
            found.append({"id": topic.id, "title": topic.title, "own": own, "cards": len(theirs),
                          "about": about, "latest": item(theirs[0]) if theirs else None})
    return sorted(found, key=lambda t: (-t["about"], t["title"]))


def people(people_: list[Person], cards: list[Card]) -> list[dict]:
    """Each person, by title, with `cards`: how many of `cards` are about them."""
    return [{**p.as_json(), "cards": sum(1 for c in cards if is_about(c, p))}
            for p in sorted(people_, key=lambda p: p.title)]


# ── Details ───────────────────────────────────────────────────────────────

def _assigned(graph: Graph, people: list[str]) -> dict:
    """The people a list item is assigned to, as an item names them: as
    written, and each with the page the graph resolves for them."""
    return {"persons": people, "person_pages": {p: graph.person_page(p) for p in people}}
