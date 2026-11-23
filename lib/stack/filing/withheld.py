"""A card's tags that its place may not carry: another person's private topic id, and whether a person set it.

    wrong = withheld_tags(["gift-ideas", "garden", "cake"], shared)    # ['gift-ideas']
    set_by_hand(versions, "gift-ideas")        # True: sam added it in Forgejo
    without_tags(card_text, {"gift-ideas"})                            # tags: garden, cake

A topic card in a person's bucket is that person's private topic
(`stack.filing.vocabulary`). The vocabulary of a card's place holds the
ids of the topic cards its readers may not read (`Vocabulary.withheld`).
A writer never puts such an id on a new card. A card already on file can
carry one: shipped migrations tagged cards against the topics of every
bucket, so a shared card that said "Gift ideas" got `gift-ideas`, the
id of a topic in alex's bucket.

The memory's repair (`stacklets.memory.place_tags.repair_private_topic_tags`,
migration memory/0013) takes such an id off with these rules:

    card                         tags before             the repair
    shared/notes/.../cake.md     gift-ideas, garden      garden
    alex/notes/.../list.md       gift-ideas, garden      unchanged: alex's bucket
    shared/notes/.../games.md    games, gift-ideas       unchanged: sam set it by hand

A tag a person set by hand stays, because a word such as `camping` can be
sam's keyword and the folder name of a private topic of alex's at once. The card's history decides (`set_by_hand`): a person's own edit
that added the tag (`stack.cards.history.Version.by_hand`), not a
filing, a bot's rebuild or a migration.

Pure: no I/O. The memory reads the files and the histories and writes
the commit.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ..cards import CARD_TYPES
from ..cards.history import Version
from ..digest import changed_by_hand, signed
from ..frontmatter import FrontmatterError, parse, with_field
from .vocabulary import Vocabulary

__all__ = ["card_tags_of", "set_by_hand", "withheld_tags", "without_tags"]


def withheld_tags(tags: Iterable[str], vocabulary: Vocabulary) -> list[str]:
    """The tags among `tags` that are the id of a topic `vocabulary`'s place may not read, in order.

        withheld_tags(["gift-ideas", "garden"], shared)       # ['gift-ideas']
        withheld_tags(["gift-ideas", "garden"], alexs)        # []
    """
    return [str(tag) for tag in tags if str(tag) in vocabulary.withheld]


def set_by_hand(versions: Sequence[Version], tag: str) -> bool:
    """Whether a person's own edit of the card added `tag`, in `versions` oldest first.

    A version counts when it is by hand (`Version.by_hand`) and holds the
    tag that the version before it did not hold. A first version by hand
    that holds it counts too.

        set_by_hand([seeded_by_the_bot, sams_edit_adding_it], "gift-ideas")   # True
        set_by_hand([filed_with_it], "gift-ideas")                            # False
    """
    before: list[str] = []
    for version in versions:
        now = card_tags_of(version.card) or []
        if version.by_hand and tag in now and tag not in before:
            return True
        before = now
    return False


def without_tags(text: str, gone: set[str]) -> str:
    """The card `text` without the tags in `gone`, every other byte as it was.

    A diary card the compile still owns is signed again (`stack.digest`),
    so the next compile leaves it alone; one a person changed stays unsigned.

        without_tags("---\\ntype: note\\ntags:\\n  - gift-ideas\\n  - garden\\n---\\n", {"gift-ideas"})
        # '---\\ntype: note\\ntags:\\n  - garden\\n---\\n'
    """
    tags = card_tags_of(text) or []
    new = with_field(text, "tags", [tag for tag in tags if tag not in gone])
    is_compiled_diary = _type_of(text) == "diary" and not changed_by_hand(text)
    return signed(new) if is_compiled_diary else new


def card_tags_of(text: str) -> list[str] | None:
    """The tags of the card `text`, or None for a file that is no card with a list of tags.

        card_tags_of("---\\ntype: note\\ntags:\\n  - garden\\n---\\n")  # ['garden']
        card_tags_of("# just a page\\n")                               # None
    """
    try:
        fields = parse(text)
    except FrontmatterError:
        return None
    tags = fields.get("tags")
    if str(fields.get("type") or "") not in CARD_TYPES or not isinstance(tags, list):
        return None
    return [str(tag) for tag in tags]


def _type_of(text: str) -> str:
    try:
        return str(parse(text).get("type") or "")
    except FrontmatterError:
        return ""
