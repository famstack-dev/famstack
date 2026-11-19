"""The name of a topic, as the family wrote it.

    name_of_topic(card_text, "itchy-scratchy-land")   # "Itchy & Scratchy Land"
    name_of_topic(None, "itchy-scratchy-land")        # "Itchy Scratchy Land"

A topic has an id and a name. The id is its folder, `itchy-scratchy-land`,
and keeps only what a path can hold. The name is the title on the topic's
card (`<bucket>/<id>/topic.md`), written from the room's name when the
archivist binds a room "Topic: Itchy & Scratchy Land", and the family's to
edit after that. Everything that shows a topic reads the name here: a title
made from the id drops every `&`, apostrophe and umlaut.

Each caller reads the card the way it reads files (the archivist through
the curator, the wiki from its working copy) and hands the text in. This
module only decides what the name is.

Pure functions, text in and text out. Stdlib only.
"""

from __future__ import annotations

from ..frontmatter import FrontmatterError, parse
from .links import topic_title

__all__ = ["name_of_topic"]


def name_of_topic(card: str | bytes | None, topic: str) -> str:
    """The topic's name: the title on its card `card`, else its id `topic` made readable.

    `card` is the topic card's text, None when there is no card. A card
    that does not parse, or has no title, names the topic by its id.

        >>> name_of_topic("---\\ntype: topic\\ntitle: Itchy & Scratchy Land\\n---\\n",
        ...               "itchy-scratchy-land")
        'Itchy & Scratchy Land'
        >>> name_of_topic(None, "itchy-scratchy-land")
        'Itchy Scratchy Land'
    """
    return _title_on(card) or topic_title(topic)


def _title_on(card: str | bytes | None) -> str:
    """The `title` of the card's frontmatter, "" when it has none or does not parse."""
    if card is None:
        return ""
    text = card.decode("utf-8", errors="replace") if isinstance(card, bytes) else card
    try:
        return str(parse(text).get("title") or "").strip()
    except FrontmatterError:
        return ""
