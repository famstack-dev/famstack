"""`stack memory diary correct`: a family member's correction applied to one card.

    rc = await correct(llm, ["--event", "$photo:simpson", "--by", "marge",
                             "--text", "that was Lisa, not Bart"])

The archivist hands a reply in a card's thread here. The correction is
applied once, to the card as it is in the memory, and committed by the
person who made it. From then on the card is the family's and the
compile leaves it alone.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from stack.ai import transcripts
from stack.ai.client import LLMError
from stack.cards.sources import now, sidecar_path
from stack.memory import CuratorDown, MemoryAPI, Refused, put, remove
from stack.people import Person as DeclaredPerson

from ... import diary_store
from ...diary.card import card_path
from ...diary.card import correct as corrected
from ...diary.card import files as card_files
from ...diary.extraction import Extraction, extraction_from
from ...diary.texts import configure_language
from .entry_reading import EXTRACT_TIMEOUT_S, report_unknown, sure_quotes
from .household_files import household_language, topic_labels, bucket_vocabulary
from .options import opt
from .progress import err
from .projection import project
from .report import card_json
from .room import remembered_room
from .store import SOURCE, cards_in, commit_parts, read_diary_tree
from .summary import SUMMARY_PROMPT

# ── Correcting a card ─────────────────────────────────────────────────
#
# A family member replies in a card's thread: "that was Lisa, not Bart".
# The archivist hands the words here. The correction is applied once, to
# the card as it is in the vault, and committed by the person who made
# it, their words in the commit. From then on the card is the family's
# and the compile leaves it alone.

_CORRECT_TOKENS = 900

_CORRECT_PROMPT = """\
A family member corrected an entry in the family archive. Apply the
correction to the entry's card and return the corrected card. Change
what the correction says is wrong and anything that follows from it;
keep everything else as it is. The correction is right wherever it
disagrees with the card or with the entry's words.

{vocabulary}

{people}

The card as it is now:
{card}

The entry's words:
{words}

The correction, from {who}:
{correction}

Reply with a JSON object with these keys, all in {language} except the
names of people and topics:
"title", "description", "summary" (one to three short sentences; when
the card names the people it could be, joined with "or", and the
correction says who it was, name that person and drop the others here,
in the title and in "persons"),
"facts" (each "Label: value", labelled by who or what it is about, such
as a family member's name or "Place", never by the kind of fact such as
"Action", "Event", "Date" or "Person"; milestones first; at most five;
none for what the summary already says), "persons" (names from the
family list only), "topics" (topics
from the vocabulary only), "quotes" (passages copied word for word from
the entry's words; keep the card's quotes unless the correction touches
them), "date": the day the entry happened as YYYY-MM-DD when the
correction states it, otherwise null, and "addressee": whom the entry is
spoken to when the correction says so (a name from the family list, or
the correction's own words such as "the kids"), otherwise null.
"""


def _card_prompt(card, declared: list[DeclaredPerson] | None, *,
                 ontology, language: str) -> str:
    """The card as the correction prompt shows it: its people by name and its
    topics by their label in the household language, not by id."""
    names = {p.id: p.first_name for p in declared or []}
    lines = [f"Title: {card.title}", f"Date: {card.on.isoformat()}",
             f"Spoken to: {card.addressee or 'no one in particular'}",
             f"Description: {card.description}",
             f"People: {', '.join(names.get(p) or p for p in card.persons)}",
             f"Topics: {', '.join(topic_labels(card.tags, ontology, language))}",
             f"Summary: {card.summary}"]
    lines += ["Facts:"] + [f"- {f}" for f in card.facts]
    if card.quotes:
        lines += ["Quotes:"] + [f"- {q}" for q in card.quotes]
    return "\n".join(lines)


def _words_prompt(card) -> str:
    lines = [f"Recorded by {card.sender.title()}:", card.body.strip() or "(no words)"]
    for who, text in card.replies:
        lines += ["", f"{who.title()} replied: {text}"]
    return "\n".join(lines)


async def correct(llm, argv: list[str]) -> int:
    """`diary correct <entry_id> --by <person> --event <event_id> --text <words>`

    Prints the corrected card as JSON. Running it again for the same
    correction event changes nothing: the event id is on the card once
    it is applied.
    """
    asked = _asked(argv)
    if asked is None:
        err("usage: diary correct <entry_id> --by <person> --event <event_id> --text <words>")
        return 2
    bucket = os.environ.get("SHARED_BUCKET", "family")
    configure_language(os.environ.get("LANGUAGE", ""))

    memory = MemoryAPI.from_container(os.environ)
    found = _card_on_file(memory, asked.entry_id, bucket=bucket)
    if found is None:
        return 1
    tree, old_path, card = found
    room_id = remembered_room()
    if asked.event_id in card.event_ids:
        err(f"  {asked.event_id} is already applied to {old_path}")
        print(json.dumps({**card_json(card, path=old_path, room_id=room_id),
                          "changed": False}, ensure_ascii=False))
        return 0

    extraction = await _read_correction(llm, card, asked, bucket=bucket)
    if extraction is None:
        return 1
    fixed = corrected(card, extraction, event_id=asked.event_id, by=asked.by,
                      words=asked.words, time=now())
    records = _commit_correction(memory, tree, old_path, fixed, asked, bucket=bucket)
    if records is None:
        return 1
    new_path = card_path(fixed, bucket=bucket)
    rc = await _pages_after(records, llm, room_id=room_id, bucket=bucket)
    print(json.dumps({**card_json(fixed, path=new_path, room_id=room_id),
                      "changed": True, "moved_from": old_path if new_path != old_path else ""},
                     ensure_ascii=False))
    return rc


# ── The steps of a correction ─────────────────────────────────────────


@dataclass(frozen=True)
class _Asked:
    """A correction as the archivist hands it over: whose words, on which card."""

    entry_id: str
    by: str
    event_id: str
    words: str


def _asked(argv: list[str]) -> "_Asked | None":
    """The correction `argv` names; None when a part of it is missing."""
    entry_id = argv[0] if argv and not argv[0].startswith("-") else ""
    by, event_id, words = (opt(argv, "--by", ""), opt(argv, "--event", ""),
                           opt(argv, "--text", "").strip())
    if not (entry_id and by and event_id and words):
        return None
    return _Asked(entry_id=entry_id, by=by, event_id=event_id, words=words)


def _card_on_file(memory, entry_id: str, *, bucket: str):
    """The diary tree in the memory and the card of `entry_id` in it, as
    (tree, path, card); None, and said why, when the curator cannot be read
    or holds no such card."""
    try:
        tree = read_diary_tree(memory, bucket)
    except (Refused, CuratorDown) as e:
        err(f"could not read the diary records from the memory: {e}")
        return None
    records = {p: t for p, (_, t) in tree.items()}
    found = [(path, card) for path, card in cards_in(records).items()
             if card.entry_id == entry_id]
    if not found:
        err(f"no diary card with entry id {entry_id}")
        return None
    return (tree, *found[0])


async def _read_correction(llm, card, asked: _Asked, *, bucket: str) -> "Extraction | None":
    """The model's reading of the card with the correction applied; None,
    and said why, when the model gave no answer that parses."""
    ontology, people, people_section, language_code, declared = bucket_vocabulary(bucket)
    prompt = _CORRECT_PROMPT.format(
        vocabulary=ontology.classifier_prompt_section(language_code),
        people=people_section,
        card=_card_prompt(card, declared, ontology=ontology, language=language_code),
        words=_words_prompt(card), who=asked.by.title(), correction=asked.words,
        language=household_language())
    try:
        raw = json.loads(await llm.complete(
            "classifier", prompt, json_mode=True, temperature=0,
            max_tokens=_CORRECT_TOKENS, timeout=EXTRACT_TIMEOUT_S))
    except (LLMError, json.JSONDecodeError, TypeError) as e:
        err(f"could not apply the correction: {e}")
        return None
    extraction = extraction_from(
        raw, ontology=ontology, language=language_code, people=people,
        declared=declared, model=os.environ.get("AI_DEFAULT_MODEL", ""))
    report_unknown(card.entry_id, extraction)
    return Extraction(**{
        **extraction.__dict__,
        "quotes": sure_quotes(extraction.quotes, card.event_ids)})


def _commit_correction(memory, tree, old_path: str, fixed, asked: _Asked, *,
                       bucket: str) -> "dict[str, str] | None":
    """Commit the corrected card as the person who corrected it, moved when
    its day changed. Returns the diary records after the commit; None, and
    said why, when the curator refused it."""
    new_path = card_path(fixed, bucket=bucket)
    written = card_files(fixed, bucket=bucket, family_owned=True)
    old_files = [p for p in (old_path, sidecar_path(old_path)) if p in tree]
    changes = ([remove(p, base=tree[p][0]) for p in old_files if p not in written]
               + [put(p, text, base=tree[p][0] if p in tree else "")
                  for p, text in written.items()])
    kind, title, body = commit_parts([fixed], new=0, verb="correct",
                                      corrections=[(asked.by, asked.words, asked.event_id)])
    try:
        memory.commit(changes, by=asked.by, kind=kind, title=title, body=body, trailers=SOURCE)
    except (Refused, CuratorDown) as e:
        err(f"could not write the corrected card: {e}")
        return None
    err(f"  corrected {new_path}" + (f" (moved from {old_path})" if new_path != old_path else ""))
    records = {p: t for p, (_, t) in tree.items() if p not in old_files}
    records.update(written)
    return records


async def _pages_after(records, llm, *, room_id: str, bucket: str) -> int:
    """The pages again, from the diary records with the corrected card."""
    _, summaries_cache = diary_store.open_stores(
        summary_fingerprint=transcripts.fingerprint(SUMMARY_PROMPT))
    return await project(records, llm, summaries_cache, room_id=room_id,
                          bucket=bucket, rebuild=False, dry_run=False)
