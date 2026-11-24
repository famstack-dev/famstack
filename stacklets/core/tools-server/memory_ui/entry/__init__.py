"""A card's sections: its own messages, drawn on its page.

A diary entry and a note filed from a chat keep the messages they were made
from as sources (ADR-018). Their page shows those messages in the order
sent, under famstack's title and summary:

    sections = sections_of(card)
    holds(sections, language="en")                   # 'Voice and 7 photos'
    narrative(card["text"])                          # the summary, without the family's words
    draw(sections, media=records, names={"homer": "Homer"}, language="en", now=now)

| Module | One concern |
|---|---|
| `sections.py` | the sources read into sections (text, voice, image, images, video, file, added) |
| `facts.py` | what the sections hold, and the text left when they are cut out |
| `view.py` | the sections as HTML |
| `made.py` | how a note was made: the ✦ mark, its key line and how to correct it |

Why a package: one view of the sources serves the diary entry's page, a
note's page and a topic's entries, so each concern has its own module and
none knows another card type's fields.
"""

from memory_ui.entry.facts import holds, narrative
from memory_ui.entry.sections import (SOURCE_TYPES, Medium, Section, date_basis, media_paths,
                                      sections_of)
from memory_ui.entry.view import draw

__all__ = ["Medium", "Section", "SOURCE_TYPES", "sections_of", "media_paths", "date_basis", "holds",
           "narrative", "draw"]
