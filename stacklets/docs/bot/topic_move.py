"""Moving a topic room's folder to the spelling its name has now.

A topic room is bound to the folder its name spelled when it was bound.
Before German letters were spelled out, "Thema: Nähprojekte" was bound to
`nahprojekte`; the rule spells it `naehprojekte` now (`stack.slug`). The
archivist moves such a topic on its next start (`ArchivistBot.on_ready`)
and posts the room's new binding. Nothing looks up the old spelling
afterwards: the code knows one spelling, and the move makes the memory
match it.

This module decides what a move changes, from the files it is given:
pure, no Matrix and no memory. `GitMirror.move_topic` reads the files,
commits the changes through the curator, and the archivist posts the
binding (`topic_rooms.rebound_state`).

What moves, for a topic `<parent>/<old>`:

  - every file under the folder `<parent>/<old>/`, to `<parent>/<new>/`
  - the topic's list `lists/<old>.md` and its archive `lists/archive/<old>/`,
    in the shared bucket and in the topic's own
  - the topic id in the `tags` and `topics` of every card that moves, and
    of every other list that names the topic
  - the topic card's title, from the name read off the folder ("Nahprojekte")
    to the room's ("Nähprojekte"); a title a person gave it stays

Card ids stay as they are: an id is a card's identity, not its spelling.
A link a person wrote into the old folder is not rewritten: it is named
for the admin (`links_into`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from stack.cards.links import topic_title
from stack.frontmatter import FrontmatterError, parse, with_field
from stack.lists import ARCHIVE_DIR, LISTS_DIR
from stack.vault import TOPIC_CARD

# The frontmatter fields that name a card's topics by id.
_TOPIC_FIELDS = ("tags", "topics")


@dataclass(frozen=True)
class TopicMove:
    """One topic's move: from `<parent>/<old>` to `<parent>/<new>`.

    >>> move = TopicMove("family", "nahprojekte", "naehprojekte", "Nähprojekte")
    >>> move.old_folder, move.new_folder
    ('family/nahprojekte', 'family/naehprojekte')
    """

    parent: str
    old: str
    new: str
    display_name: str

    @property
    def old_folder(self) -> str:
        return f"{self.parent}/{self.old}"

    @property
    def new_folder(self) -> str:
        return f"{self.parent}/{self.new}"

    def destination(self, path: str, *, shared_bucket: str) -> str | None:
        """Where `path` goes, or None when it is not the topic's to move.

        >>> move = TopicMove("homer", "fu-ball", "fussball", "Fußball")
        >>> move.destination("homer/fu-ball/notes/tor.md", shared_bucket="family")
        'homer/fussball/notes/tor.md'
        >>> move.destination("family/lists/archive/fu-ball/2026-10.md", shared_bucket="family")
        'family/lists/archive/fussball/2026-10.md'
        >>> move.destination("family/lists/fu-ball-alt.md", shared_bucket="family") is None
        True
        """
        for bucket in dict.fromkeys((shared_bucket, self.parent)):
            lists = f"{bucket}/{LISTS_DIR}"
            if path == f"{lists}/{self.old}.md":
                return f"{lists}/{self.new}.md"
            prefixes = [(f"{lists}/{ARCHIVE_DIR}/{self.old}/", f"{lists}/{ARCHIVE_DIR}/{self.new}/")]
            if bucket == self.parent:
                prefixes.append((f"{self.old_folder}/", f"{self.new_folder}/"))
            for old, new in prefixes:
                if path.startswith(old):
                    return new + path[len(old):]
        return None


def moved_text(move: TopicMove, path: str, text: str) -> str:
    """The text of a file that moves: the topic retagged, the topic card titled."""
    text = retagged(text, move.old, move.new)
    if path == f"{move.old_folder}/{TOPIC_CARD}":
        text = titled(text, topic_title(move.old), move.display_name)
    return text


def retagged(text: str, old: str, new: str) -> str:
    """`text` with the topic id `old` as `new` in its `tags` and `topics`.

    Every other byte stays as it was; a file without them comes back as it is.

    >>> retagged("---\\ntags:\\n  - nahprojekte\\n  - stoff\\n---\\n", "nahprojekte", "naehprojekte")
    '---\\ntags:\\n  - naehprojekte\\n  - stoff\\n---\\n'
    """
    fields = _frontmatter(text)
    for key in _TOPIC_FIELDS:
        values = fields.get(key)
        if isinstance(values, list) and old in values:
            renamed = [new if v == old else v for v in values]
            text = with_field(text, key, list(dict.fromkeys(renamed)))
    return text


def titled(text: str, derived: str, title: str) -> str:
    """The topic card `text` titled `title`, when its title is still the one
    read off the folder (`derived`); a title a person gave it stays.

    >>> titled("---\\ntitle: Nahprojekte\\n---\\n\\n# Nahprojekte\\n", "Nahprojekte", "Nähprojekte")
    '---\\ntitle: Nähprojekte\\n---\\n\\n# Nähprojekte\\n'
    """
    if _frontmatter(text).get("title") != derived:
        return text
    text = with_field(text, "title", title)
    return re.sub(rf"^# {re.escape(derived)}$", lambda _: f"# {title}", text, count=1, flags=re.M)


def names_topic(text: str, topic: str) -> bool:
    """True when the card names `topic` by id in its `tags` or `topics`."""
    fields = _frontmatter(text)
    return any(isinstance(fields.get(k), list) and topic in fields[k] for k in _TOPIC_FIELDS)


def links_into(text: str, move: TopicMove) -> bool:
    """True when `text` has a link into the topic's old folder.

    A link names the folder as a path segment, relative (`../nahprojekte/x.md`)
    or from the memory's root (`family/nahprojekte/x.md`), in a markdown or
    a wiki link.

    >>> move = TopicMove("family", "nahprojekte", "naehprojekte", "Nähprojekte")
    >>> links_into("[x](../../nahprojekte/notes/a.md)", move)
    True
    >>> links_into("[[family/nahprojekte/topic]]", move)
    True
    >>> links_into("[x](../naehprojekte/a.md) nahprojekte/", move)
    False
    """
    segment = rf"(?:^|/){re.escape(move.old)}/"
    targets = re.findall(r"\]\(([^)\s]+)\)|\[\[([^\]|]+)", text)
    return any(re.search(segment, a or b) for a, b in targets)


def _frontmatter(text: str) -> dict:
    try:
        return parse(text)
    except FrontmatterError:
        return {}
