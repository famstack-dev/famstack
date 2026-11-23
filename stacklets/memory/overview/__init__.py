"""The overview of the family memory for one person: the sections of its pages.

The memory's pages ("My memory", the family, a person, a topic, a sender,
the documents) show what the cards hold, built on each request with no model
call (ADR-016: the memory is read as cards). This package computes every
section of those pages from one `Graph`; core's pages only render what it
answers. The cards API serves it:

    GET /overview/me?audience=marge
    GET /overview/family?audience=marge
    GET /overview/person/<person>?audience=marge     person-bart or bart
    GET /overview/topic/<topic>?audience=marge       topic-camping
    GET /overview/sender/<sender>?audience=marge     correspondent-kwik-e-mart
    GET /overview/documents?audience=marge
    GET /overview/documents/<kind>?audience=marge    Receipt
    GET /overview/kind/<type>?audience=marge         note, diary, email, ...
    GET /overview/senders?audience=marge
    GET /overview/everything?audience=marge          the menu's counts

In code:

    from stacklets.memory.overview import NoSuchPage, overview

    graph = load_graph(vault, audience=frozenset({"marge"}))
    answer = overview(graph, "me", audience=frozenset({"marge"}), today=date(2026, 10, 1))
    answer["lists"]       # [{"id": "list-groceries", ..., "open": 2}]

**Who may see what is decided before this package.** The graph it is given
holds only the cards the audience may read (`graph.load_graph(audience=...)`,
the one place that applies `readable()`): a card in another person's own
folder is not in it, so no item, link or count here can come from one. This
package adds no rule of its own about privacy, and the pages hold none either.
What it decides is what is *relevant* to a person: the cards about them, the
lists that are theirs.

**What changed reads the graph, not git.** Each card carries its changes
from the one `git log` the graph was built with, so an overview runs no git
and costs only a pass over the cards of a cached graph.

## The answer

Every answer is one JSON object: `view` (the view asked for, `"me"` or
`"topic/topic-camping"`), `today`, `total` (how many cards the view is
about, its `counts` together) and the sections the view's page shows.

| View | Sections |
|---|---|
| `me` | person, up_next, lists, topics, changed, bookmarks, documents, senders, links, counts |
| `family` | people, up_next, lists, changed, documents, links, counts |
| `person/<id>` | person, up_next, lists, topics, changed, documents, cards, links, counts |
| `topic/<id>` | topic, up_next, cards, links, counts, bookmarks, documents, lists, changed, entries |
| `sender/<id>` | sender, cards, links, counts |
| `documents`, `documents/<kind>` | documents, senders, links, counts |
| `kind/<type>` | cards, links, counts |
| `senders` | senders, counts |
| `everything` | kinds, senders, counts |

`me` is the one person of the audience; `family` is everything the audience
may read. A person, topic or sender the audience may not read, or that does
not exist, raises `NoSuchPage` (the API answers 404), the same for both, so
a 404 does not tell that a hidden one exists. So does a document kind the
audience has no document of, a kind of card (`kind/email`) it has no
card of, `me` without exactly one person in the audience, and a view this
package does not know.

**An item** is one card as a line on a page:

    {"id": "document-0000000002", "type": "document", "title": "Car insurance",
     "day": "2026-09-02", "persons": ["Mom", "grampa"],
     "person_pages": {"Mom": "marge", "grampa": ""},
     "kind": "Policy", "sender": "Springfield Insurance",
     "sender_page": "correspondent-springfield-insurance"}   # a document
     "resource": "https://example.org/pie"                   # a bookmark

`id`, `type`, `title` and `day` are always there; `day` is the card's date,
else the day it last changed, else "". `persons` are the names as the card
writes them; `person_pages` gives each the id of the person's page, ""
when the graph has no person card for it that the audience may read, so a
page links a name only where the memory links it. `sender_page` is the
sender card's id, left out when the sender has no card.

**A part that lists items** is `{"count": <all of them>, "items": [...]}`,
newest first and cut at a page's length; `count` says how many there are.
The long list of a page (`cards` of `person/<id>`, `topic/<id>`,
`kind/<type>` and `sender/<id>`, `documents` of `documents[/<kind>]`)
comes in parts of 40, its other sections whole on every part:
`?part=2` asks for the second, and the list adds `"part": 2, "parts": 3`.
A part outside 1..parts, and `part` other than 1 on any other view, is 404.

The sections:

- `person`: `{"id": "marge", "card": "person-marge", "title": "Marge Simpson"}`.
  `card` and `title` are "" for a person without a person card.
- `people`: the person cards, each with `cards`, how many cards are about them.
- `topic`: `{"id", "title", "owner"}`; `owner` is the id of the person
  whose own folder holds the topic, "" for the family's.
- `sender`: `{"id", "title", "kinds": ["Policy"]}`.
- On `topic/<id>` only (`topic_view.py`): `bookmarks` (`count`, `items` newest
  first, each with `site` and `words`, what was sent with it); `lists`, each
  with its open count, and `items` (the first 5 open) when the list is tagged
  with the topic; `entries`, the newest 10 notes in full (`text`, `title`,
  `day`, `sender`, `sources`, `made`: whether a model wrote the text). The
  `documents` and `changed` sections read as on the person page. Older notes
  stay in the paged `cards`.
- `up_next`: what comes up in the next 60 days, soonest first: cards whose
  own `date` is after today, and open list items due from today on. A card
  without a date of its own is never here, also when it changed today. On
  `me` and a person's page, only the items assigned to that person or to no
  one: an item that names no one is its list's, and on a list that names
  no one it is everyone's. A list item comes as its list (`id`,
  `type: list`) with `title` the item's text, `list` the list's title and
  `day` the due day.
- `lists`: `[{"id", "type", "title", "day", "open": 2}]`, the lists with open
  items, never the items themselves (a long list breaks a page). A list is
  everyone's when it names no one, and the person's when it sits in their
  own folder, names them, or holds an item that is theirs, as `up_next`
  takes items. The family's calendar, where one date names Homer and
  another no one, is everyone's.
- `topics`: `[{"id", "title", "own", "cards": 12, "about": 3, "latest": item or null}]`,
  the topics the person has cards in, and their own (`own`: in the
  person's own folder; on a person's page,
  the person's own that the audience may read). `cards` counts all of
  the topic's cards the audience may read, as the topic's page does; `about`
  how many of those are about the person; `latest` is the topic's newest
  card as an item, or null for a topic with no card yet. Most `about`
  first.
- `changed`: items changed by a person in the last 14 days, newest first by
  day, then time, each with `by` (who) and `on` (the day). A bot's change is
  not shown, also one a bot committed in a person's name (a rebuild), by the
  rule `/history` reads: a migration over every card is no news.
- `bookmarks`: bookmark items, each with `resource`.
- `documents`: `{"count", "kinds": [{"kind": "Receipt", "count": 31,
  "newest": item}], "items": [...]}`, kinds with most documents first.
  `documents/<kind>` narrows `count` and `items` to that kind; `kinds` and
  `total`, the documents of every kind, stay whole, so the page keeps its tiles.
- `senders`: `[{"id", "title", "count", "kinds"}]`: the senders of the
  view's documents that have a sender card, most documents first. On
  `senders`, every sender of a document the audience may read; on
  `everything`, only how many there are: `{"count": 7}`.
- `kinds`: `[{"type": "note", "count": 12}]`, each kind of card the
  audience has cards of, in a fixed order (documents, notes, diary,
  emails, bookmarks, lists); `count` is what `kind/<type>` answers.
- `cards`: the view's cards as items.
- `links`: `{"people": [...], "topics": [...], "senders": [...]}`, each
  `{"id", "title", "count"}`, tallied over the view's cards, most first.
  A person's `id` is their id (`bart`), the others' their card id. The
  person a page is about is not among its people.
- `counts`: `{"note": 12, "document": 5}`, the view's cards by type. On
  `me` and a person's page the lists are counted as `lists` shows them,
  with an open item, so the tile and the section agree. The menu's
  `everything` counts every list, a fully ticked one too.

**The view's cards** are the cards a page is about: for `me` and a person
the cards that name the person or sit in their own folder (who filed a card
does not count: a parent files the children's papers), for a topic its
cards, for a sender its documents, for `family` every card. Person, topic
and sender cards and a list's archive months are not among them: they are
what a page links to, not lines on it. For `kind/<type>` they are the
cards of that type, for `senders` the documents that have a sender card,
and for `everything` every card.

An empty memory, or a view with nothing in it, answers every section empty.
"""

from __future__ import annotations

from .views import NoSuchPage, overview

__all__ = ["NoSuchPage", "overview"]
