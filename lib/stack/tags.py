"""A card's tags: topic ids and keywords (ADR-017 §4).

`tags` holds two kinds of word. A topic the ontology declares is tagged by
its id (`medical`), whichever language its label came in: the label a
reader sees comes from the ontology, in the instance's language, never
from the file, so two instances in two languages tag one subject alike.
Anything else is a keyword, lowercase with hyphens (`cropper-paper`),
which Obsidian takes as a tag as it stands.

    >>> ontology = Ontology.loads('[topic.medical]\\nnames = { de = "Gesundheit", en = "Medical" }\\n')
    >>> card_tags(["Gesundheit", "Cropper Paper", "medical", "Moe's"], ontology)
    ['medical', 'cropper-paper', 'moe-s']
    >>> tag_label("medical", ontology, "de")
    'Gesundheit'

One rule, `tag_for`, and a surface for each reader of it:

- `card_tags`: what a writer writes. A tag the rule cannot read as a
  keyword is slugged.
- `with_topic_tags`: one existing file brought to that shape (migrations
  `memory/0005`, and `memory/0010` for the diary). A tag the rule cannot
  read stays and is reported: the migration does not guess.
- `is_tag`: whether a tag already has its shape (the format check).
- `tag_label`, `tag_words`: a topic shown and found by its labels.

Pure functions, text in and text out. Stdlib only.
"""

from __future__ import annotations

import re
from typing import Iterable

from .cards import CARD_TYPES
from .frontmatter import FrontmatterError, parse, with_field
from .ontology import Ontology, Topic


# A keyword: words of lowercase letters and digits joined by single
# hyphens, at least one letter among them, since Obsidian takes no tag
# of digits alone.
_KEYWORD = re.compile(r"(?=[\w-]*[^\W\d_])[^\W_]+(?:-[^\W_]+)*")

# What separates a tag's words as people and classifiers write them.
_SEPARATORS = re.compile(r"[\s_-]+")

# What a slug keeps of a tag: its letters and digits.
_NOT_A_LETTER = re.compile(r"[\W_]+")


# ── The rule ──────────────────────────────────────────────────────────────

def tag_for(tag: str, ontology: Ontology) -> str | None:
    """How `tag` is written on a card, or None when the rule cannot read it.

    A topic's id or its label in any of the ontology's languages, in any
    case and with spaces, hyphens or underscores between its words,
    becomes the id. Anything else is lowercased with hyphens between its
    words, and is a keyword when that leaves only letters and digits.

    >>> ontology = Ontology.loads('[topic.tax_office]\\nnames = { de = "Finanzamt", en = "Tax Office" }\\n')
    >>> [tag_for(t, ontology) for t in ("Tax Office", "finanzamt", "tax-office", "Cropper Paper")]
    ['tax_office', 'tax_office', 'tax_office', 'cropper-paper']
    >>> [tag_for(t, ontology) for t in ("Moe's", "2026", "Person: Homer")]
    [None, None, None]
    """
    words = _words(str(tag))
    if not words:
        return None
    if topic := _topic_named(words, ontology):
        return topic.id
    keyword = words.replace(" ", "-")
    return keyword if _KEYWORD.fullmatch(keyword) else None


def is_tag(tag: str, ontology: Ontology) -> bool:
    """Whether `tag` is written the way the rule writes it: a topic id or a keyword."""
    return tag_for(tag, ontology) == tag


# ── Writers ───────────────────────────────────────────────────────────────

def card_tags(tags: Iterable[str], ontology: Ontology) -> list[str]:
    """The tags a writer writes for `tags`, in their order, each once.

    A tag the rule cannot read is slugged first, its letters and digits
    joined by hyphens (`Moe's` becomes `moe-s`), and dropped when no
    letter is left (`2026`).
    """
    written = (tag_for(t, ontology) or tag_for(_slug(str(t)), ontology) for t in tags)
    return list(dict.fromkeys(t for t in written if t))


# ── Existing files ────────────────────────────────────────────────────────

def tags_by_id(tags: Iterable[str], ontology: Ontology) -> tuple[list[str], list[str]]:
    """`tags` as the rule writes them, each once, and those it left as they are.

    A tag the rule cannot read keeps its place and is returned, to be
    reported: a person wrote it, and no slug is a sure reading of it.
    """
    kept: list[str] = []
    left: list[str] = []
    for tag in map(str, tags):
        written = tag_for(tag, ontology)
        if written is None:
            left.append(tag)
        kept.append(written or tag)
    return list(dict.fromkeys(kept)), left


def with_topic_tags(text: str, ontology: Ontology) -> tuple[str, list[str]]:
    """The card `text` with its tags as the rule writes them, and the tags it left.

    Only the `tags` field changes; every other byte stays as it was. A
    file that is no card and frontmatter that does not parse come back
    unchanged. A signed card (`stack.digest`) keeps the digest it had:
    whether to sign it again is for the migration that owns it to decide
    (`memory/0010` signs a diary card the compile still owns).
    """
    try:
        fields = parse(text)
    except FrontmatterError:
        return text, []
    tags = fields.get("tags")
    if str(fields.get("type") or "") not in CARD_TYPES or not isinstance(tags, list):
        return text, []
    written, left = tags_by_id(tags, ontology)
    if written != tags:
        text = with_field(text, "tags", written)
    return text, left


# ── Readers ───────────────────────────────────────────────────────────────

def tag_label(tag: str, ontology: Ontology, language: str) -> str:
    """What a reader sees for `tag`: a topic's label in `language`, else the tag."""
    topic = ontology.topics.get(tag)
    return topic.name(language) if topic else tag


def tag_words(tag: str, ontology: Ontology) -> list[str]:
    """The words a search finds `tag` by: a topic's id and its label in every
    language, whichever of them the card holds; else the tag itself.

    A card reads the same to a search before and after its tags became ids.

    >>> ontology = Ontology.loads('[topic.medical]\\nnames = { de = "Gesundheit", en = "Medical" }\\n')
    >>> tag_words("Gesundheit", ontology) == tag_words("medical", ontology)
    True
    """
    topic = ontology.topics.get(tag_for(tag, ontology) or "")
    if topic is None:
        return [str(tag)]
    return list(dict.fromkeys([topic.id, *topic.names.values()]))


# ── Helpers ───────────────────────────────────────────────────────────────

def _words(text: str) -> str:
    """`text` lowercased, its words one space apart."""
    return _SEPARATORS.sub(" ", text).strip().lower()


def _topic_named(words: str, ontology: Ontology) -> Topic | None:
    """The topic whose id, else whose label in some language, reads as `words`.

    Ids first: an id is one topic's, while a label may repeat another
    topic's id.
    """
    for topic in ontology.topics.values():
        if _words(topic.id) == words:
            return topic
    for topic in ontology.topics.values():
        if any(_words(name) == words for name in topic.names.values()):
            return topic
    return None


def _slug(text: str) -> str:
    return _NOT_A_LETTER.sub("-", text.lower()).strip("-")
