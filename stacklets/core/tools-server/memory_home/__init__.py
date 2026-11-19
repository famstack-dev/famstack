"""The memory's pages on core's tools server: "My memory" for the person on
the paired device (`/memory`), the family's page (`/memory/family`), and a
page for each person, topic and sender, for the documents by kind, for
each kind of card and for the senders,
rendered here from the memory's overview (`/overview/<view>` of the memory
stacklet's cards API), in the card pages' look, phone first.

The web server routes each address to one entry and sends its answer:

    import memory_home

    memory_home.routes(app, reads=..., language=..., never_set_up=..., answer=...)

    looking = memory_home.Looking(kind="person", person="marge")
    answer = await memory_home.open_mine(reads, looking, language="en", never_set_up=never_set_up)
    answer = await memory_home.open_family(reads, looking, language="en", never_set_up=never_set_up)
    answer = await memory_home.open_search(reads, looking, "dentist", today=today, language="en",
                                           never_set_up=never_set_up)
    answer = await memory_home.open_card("note-7e1a2b3c4d", reads, looking, now=now, language="en",
                                         paperless="", never_set_up=never_set_up)
    status, preview = await memory_home.open_preview(reads, "note-7e1a2b3c4d", today=today, language="en")
    kind, body = memory_home.asset("home.css")

    routes.py       the routes under `/memory`, added by the web server
    pages.py        "My memory", the family, the search, the preview: the reads and the answer
    graph_pages.py  the person, topic, sender, documents, kind and senders pages: the reads and the answer
    card_pages.py   a card's page (`/card/<ref>`) in the memory's frame: the header and the menu
    menu.py         what the menu lists: the person's topics, every kind of card, the senders
    looking.py      who looks, and which view and reads "My memory" is for them
    notices.py      the "not found" page, and the page for a memory that does not answer
    my_page.py      "My memory": the greeting and the person's sections
    family_page.py  the family's page and its sections
    person_page.py, topic_page.py, sender_page.py, documents_page.py, kind_page.py, senders_page.py
                    the graph pages: one person, one topic, one sender, the documents,
                    one kind of card, the senders
    search_page.py  the search's results, grouped by type
    sections.py     each section, drawn from one part of the overview
    items.py        one card as a line, and what it links to
    frame.py        the header with the search, the menu, the phone's menu button
    words.py        the pages' words, English and German
    addresses.py    every address of the memory's pages
    static_files.py, static/   the pages' own stylesheet, their small script and
                    the card links' preview (`preview.js`)

**The memory computes, core renders.** Which cards a device may see, which
are a person's, every count, and which page a person's name, a tag or a
sender opens are the memory's answer for the device's audience
(`memory_ui.Reads`, the audience from `devices.who`); nothing here
filters, counts or matches a name by itself. The card pages (`memory_ui`) stay the page of
each card: every card line links there, and `card_pages.py` hands them
the memory's frame, so a card's page keeps the header and the menu. This
package uses their frame, icons, words and reads, and none of them
imports it.
"""

from __future__ import annotations

from memory_home.card_pages import card_looking, no_card, open_card
from memory_home.graph_pages import (open_documents, open_kind, open_person, open_sender, open_senders,
                                     open_topic)
from memory_home.looking import Looking, view_of
from memory_home.pages import open_family, open_mine, open_preview, open_search
from memory_home.routes import routes
from memory_home.static_files import asset

__all__ = ["Looking", "open_mine", "open_family", "open_person", "open_topic", "open_sender",
           "open_documents", "open_kind", "open_senders", "open_search", "open_preview", "view_of", "asset", "routes",
           "open_card", "no_card", "card_looking"]
