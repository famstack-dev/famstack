"""Each entry's card, with the model asked only where a card needs it.

    cards = await cards_for(entries, llm, cache, bucket="family", media=kept,
                             on_file=on_file, rebuild=False, force=False)

The model reads an entry with no card yet and one whose card was made
from an earlier version of it. A card that stands is carried on as it
is on file, and an entry whose card a person changed is never read.
"""

from __future__ import annotations

from stack import media
from stack.filing.topic_list import prompt_section

from ...diary.card import edited_by_hand, entry_id_for, to_card
from ...diary.extraction import Extraction
from ...diary.plan import cards_on_file, same_sources
from .entry_reading import extract_all, has_words, current_template
from .household_files import bucket_vocabulary


async def cards_for(entries, llm, extractions_cache, *, bucket: str,
                     media: "dict[str, str]", on_file: "dict[str, str]",
                     rebuild: bool, force: bool) -> list:
    """Each entry's card, the model asked only where a card needs it.

    The model reads an entry with no card yet and one whose card was made
    from an earlier version of it (`same_sources`). With
    `rebuild` it reads every entry whose card the compile owns, under the
    current prompts. A card that stands is passed on as it is on file, and
    an entry whose card a person changed is never read: `plan` keeps the
    card whatever the model would say.
    """
    sha = _media_sha()
    bare = [to_card(e, Extraction(), media=media, media_sha=sha)
            for e in entries]
    standing = standing_cards(bare, on_file, rebuild=rebuild)
    to_read = [e for e, card in zip(entries, bare) if card.entry_id not in standing]
    topics, people, people_section, language_code, declared = bucket_vocabulary(bucket)
    extractions = await extract_all(
        to_read, llm, extractions_cache, topics=topics, people=people, declared=declared,
        vocabulary=prompt_section(topics, language_code),
        people_section=people_section, language_code=language_code,
        current=rebuild, force=force)
    read = {entry_id_for(e.event_ids[0]): (e, x)
            for e, x in zip(to_read, extractions)}
    return [to_card(*read[card.entry_id], media=media, media_sha=sha)
            if card.entry_id in read else standing[card.entry_id] or card
            for card in bare]


def _media_sha():
    """The hash the media store recorded for an archived file, by its link
    (`stack.media.recorded_sha256`); "" for every file without a store."""
    root = media.open_archive()
    return (lambda link: media.recorded_sha256(root, link)) if root is not None \
        else (lambda link: "")


def standing_cards(cards, on_file: "dict[str, str]", *, rebuild: bool) -> "dict[str, object]":
    """The entries whose card on file stays as it is, by entry id.

    The value is the card as it is on file, for the compile to carry on
    with, or None for a card a person changed: the compile keeps it, and
    goes on with the entry as it is in the room so `plan` can tell
    whether the room moved on under it.
    """
    by_entry = cards_on_file(on_file)
    out: dict[str, object] = {}
    for card in cards:
        path, before = by_entry.get(card.entry_id, ("", None))
        if before is None:
            continue
        if edited_by_hand(on_file[path]):
            out[card.entry_id] = None
            continue
        if not rebuild and same_sources(before, card):
            out[card.entry_id] = before
    return out


def made_under_older_prompts(entries, on_file: "dict[str, str]", plan, *, bucket: str) -> int:
    """How many cards the compile owns and keeps were made under a prompt
    template other than the one an entry is read under now."""
    by_entry = cards_on_file(on_file)
    older = 0
    for entry in entries:
        path, before = by_entry.get(entry_id_for(entry.event_ids[0]), ("", None))
        if before is None or path in plan.writes or path in plan.deletes:
            continue
        if edited_by_hand(on_file[path]):
            continue
        # An entry without words is never read, so its card carries no
        # template and no prompt can be older than it.
        if has_words(entry) and before.prompt_template != current_template(entry):
            older += 1
    return older
