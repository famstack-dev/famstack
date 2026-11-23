"""The tags of the cards on file, read for each card's place: the vocabulary per bucket, and the repair of private topic ids.

    vocabulary = vocabularies(vault, shared_bucket="family")
    vocabulary("homer/emails/2026/11/lanes-7c3e9f.md").withheld   # {'surprise-party'}

    found, sha = repair_private_topic_tags(memory, vault, shared_bucket="family",
                                           trailer="Migration: memory/0013")
    found.changes    # path to the card without another person's topic id
    found.left       # a card where a person set that id by hand, for the admin

A topic card in a person's bucket is that person's private topic
(`stack.filing.vocabulary`). A card is tagged only with the topics its
place may read, so its readers never see the topic of someone else. The
archivist and the diary write new cards this way.

The passes over the cards on file read the same vocabulary, one per
bucket (`vocabularies`): `topic_tags_for` and `diary_topic_tags_for`
(migrations memory/0005 and memory/0010, through the memory's lib) and
memory/0011. Their first runs read the
ontology of every bucket instead, so a family card that said
"Homers 40th" got the id of Marge's topic `surprise-party`.

Migration memory/0013 repairs that (`repair_private_topic_tags`). The
rules for one card are pure and live in the framework
(`stack.filing.withheld`); this module reads the files and the
histories, and writes the one commit:

    card                                tags before                 after
    family/notes/.../cake.md            surprise-party, van, cake   van, cake
    homer/emails/.../lanes.md           surprise-party, health      health
    marge/notes/.../balloons.md         surprise-party, van         unchanged
    family/notes/.../bowling.md         bowling, surprise-party     unchanged, named in `left`
      (Homer added the id by hand in Forgejo)

A tag a person set by hand stays, and so does one on a card without a
`card_id` or whose history the curator does not give: the output names
each, for the admin.

It lives beside the memory's lib and not in `migrations/`, as
`old_diary_cards` does, because a module there runs on the host
(ADR-019). It keeps the per-place tag passes together, out of the lib.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Optional

from stack.cards.history import Version
from stack.digest import changed_by_hand, signed
from stack.filing.vocabulary import Vocabulary
from stack.filing.withheld import card_tags_of, set_by_hand, withheld_tags, without_tags
from stack.frontmatter import FrontmatterError, parse
from stack.memory import MemoryAPI, Refused
from stack.tags import with_topic_tags

from .lib import (DEFAULT_SHARED_BUCKET, SEED_ONTOLOGY_PATH, CardLinks, _markdown_files, _parse_frontmatter,
                  _read_with_version, commit_found)

__all__ = ["diary_topic_tags_for", "private_topic_repair_for", "repair_private_topic_tags",
           "topic_tags_for", "vocabularies"]

# Whether a person set a tag on the card with an id by hand: True, False,
# or None when the card's history cannot be read.
SetBy = Callable[[str, str], Optional[bool]]


def vocabularies(vault: Path, *, shared_bucket: str) -> Callable[[str], Vocabulary]:
    """The vocabulary of a card's place, for a pass over every card of `vault`.

    The function returned takes a vault path and returns `Vocabulary.of`
    for its bucket. It reads each bucket once, because a pass asks for
    every card and a bucket has one vocabulary. An `ontology.toml` that
    cannot be read raises on the first call, as `Vocabulary.of` does.

        vocabulary = vocabularies(vault, shared_bucket="family")
        vocabulary("family/notes/2026/11/cake-4b2d1a.md").ontology    # the family's topics
        vocabulary("marge/notes/2026/11/balloons-9a1c2e.md")          # the family's and Marge's
    """
    known: dict[str, Vocabulary] = {}

    def of(path: str) -> Vocabulary:
        bucket = path.strip("/").partition("/")[0]
        if bucket not in known:
            known[bucket] = Vocabulary.of(bucket, vault, seed=SEED_ONTOLOGY_PATH,
                                          shared_bucket=shared_bucket)
        return known[bucket]

    return of


# ── Tagging each card with its place's topics ──

def topic_tags_for(vault: Path, *, shared_bucket: str = DEFAULT_SHARED_BUCKET) -> CardLinks:
    """What tags the topics of the working copy `vault`'s cards by id.

    Each tag that names a topic of the card's place (`ontology.toml`, else
    the shipped seed, and the topic cards its readers may read:
    `vocabularies`), by its id or its label in any language,
    becomes the id; every other tag is lowercased with hyphens; two that
    become one are one, in the place of the first. A private topic's title
    on a card in another bucket stays a keyword. A tag that is no keyword
    even so stays as it is and is listed in `left`
    (`stack.tags.with_topic_tags`). Diary cards are left alone: they are
    `diary_topic_tags_for`'s.

        found = topic_tags_for(vault, shared_bucket="family")
        found.changes["family/notes/2026/11/cake-4b2d1a.md"]
        # tags: Homers 40th, Camper Van  ->  homers-40th, van
        found.changes["marge/notes/2026/11/balloons-9a1c2e.md"]
        # tags: Homers 40th  ->  surprise-party: her own bucket
    """
    vocabulary = vocabularies(vault, shared_bucket=shared_bucket)
    texts = _markdown_files(vault)
    found = CardLinks()
    for path in sorted(texts):
        if _parse_frontmatter(texts[path], path).get("type") == "diary":
            continue
        text, left = with_topic_tags(texts[path], vocabulary(path).ontology)
        found.left += [f"{path}: tag {tag!r} is no topic and no keyword" for tag in left]
        if text != texts[path]:
            found.changes[path] = text
    return found


def diary_topic_tags_for(vault: Path, *, shared_bucket: str = DEFAULT_SHARED_BUCKET) -> CardLinks:
    """What tags the topics of the working copy `vault`'s diary cards by id.

    The rule of every other card (`topic_tags_for`): a topic of the
    card's place, by its label in any language, becomes its id, every
    other tag is lowercased with hyphens, and a tag that is no keyword
    even so stays and is listed in `left`. A card the compile still owns is signed again
    (`stack.digest.signed`), so it is what the compile writes today; a
    card a person changed gets ids too, and stays unsigned and theirs.

        found = diary_topic_tags_for(vault, shared_bucket="family")
        found.changes["family/diary/2026/11/2026-11-14.md"]
        # tags: Camper Van  ->  van, signed again
    """
    vocabulary = vocabularies(vault, shared_bucket=shared_bucket)
    found = CardLinks()
    for path, text in sorted(_markdown_files(vault).items()):
        if _parse_frontmatter(text, path).get("type") != "diary":
            continue
        new, left = with_topic_tags(text, vocabulary(path).ontology)
        found.left += [f"{path}: tag {tag!r} is no topic and no keyword" for tag in left]
        if new != text:
            found.changes[path] = new if changed_by_hand(text) else signed(new)
    return found


# ── Taking another person's topic off ──

def private_topic_repair_for(vault: Path, *, shared_bucket: str, set_by: SetBy) -> CardLinks:
    """What takes another person's private topic id off the cards of the working copy `vault`.

    On each card, the ids of topic cards its place may not read
    (`stack.filing.withheld`) go; every other byte stays. `set_by(card_id,
    tag)` says whether a person set the tag by hand: such a tag, and one
    whose history is unknown (None), stays and is listed in `left`. A card
    in the bucket of the topic's person is never changed. A second run
    finds nothing.

    Each changed card is read once with its version (`found.versions`), so
    an edit that reaches the clone before the commit makes the curator
    refuse it (`commit_found`) instead of being overwritten.

        found = private_topic_repair_for(vault, shared_bucket="family", set_by=lambda c, t: False)
        found.changes["family/notes/2026/11/cake-4b2d1a.md"]     # the card, tags: van, cake
    """
    vocabulary = vocabularies(vault, shared_bucket=shared_bucket)
    found = CardLinks()
    for path in sorted(_markdown_files(vault)):
        text, version = _read_with_version(vault, path)
        wrong = withheld_tags(card_tags_of(text) or [], vocabulary(path))
        if not wrong:
            continue
        card_id = _card_id_of(text)
        answers = {tag: set_by(card_id, tag) for tag in wrong}
        found.left += [f"{path}: tag {tag!r} is the topic of another person's bucket, {_why_kept(card_id, by_hand)}"
                       for tag, by_hand in answers.items() if by_hand is not False]
        if gone := {tag for tag, by_hand in answers.items() if by_hand is False}:
            found.changes[path] = without_tags(text, gone)
            found.versions[path] = version
    return found


def repair_private_topic_tags(memory: MemoryAPI, vault: Path, *, shared_bucket: str,
                              trailer: str = "") -> tuple[CardLinks, Optional[str]]:
    """Take another person's private topic id off `vault`'s cards (`private_topic_repair_for`).

    One commit by the memory bot through the curator, each card written on
    the version the pass read; an edit made meanwhile makes the curator
    refuse it (`stack.memory.Refused`), and nothing is written. Reads the
    history of each card that carries such an id from the curator
    (`MemoryAPI.versions`), so a tag a person set by hand stays. Returns
    what the pass found and the commit's sha, or None when nothing changed.

        found, sha = repair_private_topic_tags(memory, vault, shared_bucket="family",
                                               trailer="Migration: memory/0013")
    """
    def set_by(card_id: str, tag: str) -> Optional[bool]:
        if not card_id:
            return None
        try:
            history = memory.versions(card_id)
        except Refused:
            return None
        return set_by_hand([Version.from_json(v) for v in history.versions], tag)

    found = private_topic_repair_for(vault, shared_bucket=shared_bucket, set_by=set_by)
    title = f"take another person's topic off {len(found.changes)} card(s)"
    return found, commit_found(memory, vault, found, title=title, trailer=trailer)


def _why_kept(card_id: str, by_hand: Optional[bool]) -> str:
    if not card_id:
        return "and the card has no card_id"
    return "set by a person" if by_hand else "and the curator gives no history of the card"


def _card_id_of(text: str) -> str:
    try:
        return str(parse(text).get("card_id") or "")
    except FrontmatterError:
        return ""
