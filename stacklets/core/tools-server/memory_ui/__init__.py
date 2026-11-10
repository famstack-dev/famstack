"""The card pages on core's tools server: one page for each card of the
family memory (`/card/<card id>`), rendered here from the memory's cards
API, in the family wiki's look. A chat link to a card
(`/go/card/<card id>`) opens it.

The web server routes each address to one entry and sends its answer:

    import memory_ui

    reads = memory_ui.Reads("http://stack-memory-curator:8090")
    answer = await memory_ui.open_card("note-0b6e2d91aa", reads, now=now, language="en",
                                       paperless="", never_set_up=never_set_up)
    answer.status, answer.html, answer.location

    pages.py       one entry per page: reads, lands, renders, answers
    addresses.py   every address of the pages: a card, its history, a file
    words.py       the pages' words (English, German) and dates in plain words
    text.py        a card's markdown as safe HTML
    card_types.py  what each card type shows at the top of its page
    file_notes.py  a note made from a file: its file, kind, text or caption
    lists.py       a list's body: its sections, items, archive months
    entry.py       a diary entry's sections, in the order they were sent
    card_page.py   a card's page, one layout for every type
    preview.py     the line a chat app previews a card's link with
    notices.py     the pages in place of a card: removed, or no memory yet
    history.py     a card's history as a timeline, loaded when opened
    frame.py       the frame every page sits in, and the line icons
    reads.py       what the pages read from the memory (the I/O edge)
    static/        the stylesheet and the small script

Where an address lands (a former id, a file path, a removed card, no
card) is decided before a page is rendered (`pages.landing`,
`pages.open_card`); `/go/card/<card id>` comes straight here, so a card id is
resolved in this one place. Only `addresses.py` spells an address; a
test keeps the other modules from it.

Dependencies go one way: the web server and the resolver import this
package, and nothing in it imports them. The resolver needs only
`addresses.py` (a card's address, a Paperless document's page), which
imports nothing of the pages.
"""

from memory_ui.pages import Answer, no_card, open_card, open_history, open_text
from memory_ui.reads import Reads

__all__ = ["Answer", "Reads", "open_card", "open_history", "open_text", "no_card"]
