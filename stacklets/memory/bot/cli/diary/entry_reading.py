"""The model's reading of an entry for its card: title, summary, facts, people.

    extractions = await extract_all(entries, llm, cache, ontology=ontology, ...)
    current_template(entry)    # the fingerprint of the prompt this entry is read under

One call per entry, after the entries are compiled, because the entry is
the unit a card describes. The answer is held to the household's
topics and people (`bot/diary/extraction.py`). A card records the
prompt template it was read under, so a new prompt shows as cards made
under older prompts and rewrites nothing.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import replace

from stack.ai import transcripts
from stack.ai.client import LLMError
from stack.ai.transcripts import TRANSCRIPTS
from stack.briefing import is_short_text
from stack.people import Person as DeclaredPerson

from ... import call_costs
from ...diary.card import entry_id_for
from ...diary.extraction import extraction_from
from .household_files import household_language
from .progress import err

# ── Reading an entry for its card ─────────────────────────────────────
#
# One call per entry, after the room has been read and the entries
# compiled: the entry is the unit a card describes, so it is the unit the
# model reads. A recording joined from two uploads is read once, whole,
# with its replies. What comes back is held to the household's vocabulary
# (`extraction_from`) before it touches a card.


_EXTRACT_TOKENS = 900
EXTRACT_TIMEOUT_S = 180.0

_EXTRACT_PROMPT = """\
You are filing one entry from a family's private memories room into the
family archive. Report what the entry says. Never translate its words,
never add to them. When you quote, copy the words exactly.

{vocabulary}

{people}

The entry:
{entry}

The people in it: name them, in the title, the description, the summary
and "persons", and never describe someone by their place in the family
instead. The entry says who recorded it. When the words name someone
else who took part, or address them by name, name that person. When
they show that someone else took part but not who, that person is not
known, and naming one of them as if it were is wrong. Name the people it
could be instead: every family member from the list the words do not
rule out (by how they speak to the one recording, what they do, how old
they must be), joined with "or". Never pick one because they seem more
likely. A family member corrects the card when it is wrong.

Reply with a JSON object with these keys:

"title": a short name for the entry, three to six words, in {language}.
  Name what happened, not the medium: "Bart's first bike ride", not
  "Voice note".

"description": one sentence in {language} saying what this entry is.

{summary}

{facts}

"persons": the family members the entry is about, spoken to or who took
  part, using the names from the family list above; when the words do
  not say who took part, the people it could be. Only names from that
  list.

"topics": up to three topics from the vocabulary above that fit the
  entry. Only topics from that vocabulary. An empty list when none fits.

"quotes": when the entry's words are longer than about 100 words: up to
  three short passages copied word for word from them, complete
  sentences only, the lines most worth keeping. Otherwise an empty list.
"""


_SUMMARY_FIELD = """\
"summary": one to three short sentences in {language} saying what the
  entry is about, naming its people as above. Write with verbs, as
  things that happened, never as a list of topics. In a recorded
  conversation, say who took part and what it was about, never who said
  which line. No marketing words.
  Wanted register (an invented example, not this family):
    "Anna and Jonas or Mia talk about the day at the lake and the shoe
    that landed in the water."
  Not wanted: "Anna and Jonas report on an outing, a picnic, a lost
  shoe and the weather.\""""

_FACTS_FIELD = """\
"facts": what someone would want to look up about this entry later, in
  {language}, each as "Label: value". The label is who or what the fact
  is about: a family member's name ("Maggie: first tooth"), a place
  ("Place: Springfield Lake"), a thing ("Swimming badge: 25 metres"),
  never the kind of fact ("Action", "Event", "Detail", "Date", "Person").
  Milestones first: a first step, a first word, a badge, a birthday, a
  new school. Leave out what the summary already says in a sentence and
  the day the entry happened. At most five. An empty list when the entry
  holds nothing worth looking up, which is often."""

# A short entry's card shows its words, and a summary of them would be as
# long as they are (`stack.briefing.is_short_text`). The card (`to_card`) drops
# one the model writes anyway.
_SHORT_SUMMARY_FIELD = """\
"summary": an empty string. The entry is short and its card shows its
  words as they are, so a summary would only repeat them."""

_SHORT_FACTS_FIELD = _FACTS_FIELD + """
  The card shows the entry's words, so a fact is only worth it for what
  someone would look up on its own: a milestone, a date, an amount, a
  name. Never a line that says part of the entry again."""


def _reading_fields(entry, language: str) -> dict[str, str]:
    """The prompt's summary and facts keys, for a short or a long entry."""
    short = is_short_text(entry.body)
    return {
        "summary": (_SHORT_SUMMARY_FIELD if short else _SUMMARY_FIELD).format(language=language),
        "facts": (_SHORT_FACTS_FIELD if short else _FACTS_FIELD).format(language=language),
    }


def _entry_prompt(entry) -> str:
    """One entry as the model reads it: who, when, what, and the words."""
    when = ("date unknown" if entry.confidence == "uncertain"
            else entry.on.isoformat())
    kind = {"voice": "voice recording", "image": "photo", "video": "video",
            "file": "file"}.get(entry.kind, "written note")
    if entry.kind == "voice" and entry.mode == "dialogue":
        kind = "recorded conversation (no speaker labels)"
    lines = [f"Recorded by {entry.sender.title()}, {when}, {kind}."]
    if entry.addressee:
        lines.append(f"Spoken to: {entry.addressee}.")
    lines.append("")
    lines.append(entry.body.strip() or "(no words)")
    for who, text in entry.comments:
        lines += ["", f"{who.title()} replied: {text.strip()}"]
    return "\n".join(lines)


def _extract_prompt(entry, *, vocabulary: str, people_section: str) -> str:
    """The prompt that reads one entry for its card."""
    language = household_language()
    return _EXTRACT_PROMPT.format(
        vocabulary=vocabulary, people=people_section or "",
        entry=_entry_prompt(entry), language=language,
        **_reading_fields(entry, language))


def _unsure_words(event_ids) -> set[str]:
    """Words whisper flagged as unsure in any of these recordings."""
    unsure: set[str] = set()
    for event_id in event_ids:
        record = TRANSCRIPTS.read(event_id) or {}
        for w in (record.get("quality") or {}).get("low_words") or []:
            if word := (w.get("word") or "").strip().lower():
                unsure.add(word)
    return unsure


def sure_quotes(quotes, event_ids) -> list[str]:
    """Drop quotes holding a word whisper was unsure of.

    A quote is the most prominent text on a diary page; it must not
    showcase a word the recognizer flagged.
    """
    unsure = _unsure_words(event_ids)
    if not unsure:
        return list(quotes)
    return [q for q in quotes
            if not set(re.findall(r"[\wÀ-ɏ]+", q.lower())) & unsure]


async def extract_all(entries, llm, cache, *, ontology, people: dict,
                       declared: list[DeclaredPerson] | None,
                       vocabulary: str, people_section: str,
                       language_code: str, current: bool = False,
                       force: bool = False) -> list:
    """What a model reads out of each entry, cached per entry.

    The cache is keyed by the entry's sources (`_sources`), not by the
    prompt or the vocabulary it offers, so a reading on file for the
    entry as it is now is used whatever prompts made it. `current` takes
    only a reading made under the current prompt template (`current_template`);
    `force` takes none. An entry the model fails on still gets an empty
    extraction: its card is written with a plain title and the family's
    words, and a later run fills in the rest. An entry with no words and
    no replies is never sent to the model: it has nothing to read.
    """
    model = os.environ.get("AI_DEFAULT_MODEL", "")

    def on_file(entry) -> "dict | None":
        return _on_file(entry, cache, current=current, force=force)

    out = []
    todo = sum(1 for e in entries if on_file(e) is None)
    if todo:
        err(f"  reading {todo} entr{'y' if todo == 1 else 'ies'} for their cards")
    for entry in entries:
        entry_id = entry_id_for(entry.event_ids[0])
        raw, template = await _answer_for(entry, on_file(entry), llm, cache,
                                          vocabulary=vocabulary, people_section=people_section)
        extraction = extraction_from(
            raw, ontology=ontology, language=language_code, people=people,
            declared=declared, model=model)
        report_unknown(entry_id, extraction)
        out.append(replace(extraction, template=template,
                           quotes=sure_quotes(extraction.quotes, entry.event_ids)))
    return out


def _on_file(entry, cache, *, current: bool, force: bool) -> "dict | None":
    """The reading the cache holds for the entry as it is now; None with
    `force`, or when `current` asks for the current template and it is older."""
    if force:
        return None
    return cache.get(entry_id_for(entry.event_ids[0]), _sources(entry),
                     current_template(entry) if current else None)


async def _answer_for(entry, raw, llm, cache, *, vocabulary: str,
                      people_section: str) -> "tuple[object, str]":
    """The model's answer for an entry and the prompt template it was read
    under: `raw` from the cache when it holds one, else a new answer, kept
    in the cache. The template is "" for no answer."""
    template = str((raw or {}).get("template") or "")
    if raw is not None:
        return raw, template
    if not has_words(entry):
        # No words and no replies: the card gets its plain title (kind
        # and day) and the model has nothing to read. A reading made
        # under the prompt is not recorded, so a later reply, which
        # changes the entry's sources, reads it then.
        return None, ""
    template = current_template(entry)
    raw = await _ask_model(entry, llm, vocabulary=vocabulary, people_section=people_section)
    if not isinstance(raw, dict):
        return raw, ""
    cache.put(entry_id_for(entry.event_ids[0]), raw, _sources(entry), template)
    cache.save()
    return raw, template


async def _ask_model(entry, llm, *, vocabulary: str, people_section: str):
    """The model's answer for one entry, parsed; None, and said why, when
    the call failed or the answer is not JSON."""
    try:
        answer = await llm.complete(
            "classifier", _extract_prompt(entry, vocabulary=vocabulary,
                                          people_section=people_section),
            json_mode=True, temperature=0,
            max_tokens=_EXTRACT_TOKENS, timeout=EXTRACT_TIMEOUT_S,
            on_cost=call_costs.recorder("diary-entry"))
        return json.loads(answer)
    except (LLMError, json.JSONDecodeError, TypeError) as e:
        err(f"  could not read the entry {entry_id_for(entry.event_ids[0])}: {e}")
        return None


def has_words(entry) -> bool:
    """Whether an entry has anything for a model to read: its own words or a reply."""
    return bool(entry.body.strip() or entry.comments)


def _sources(entry) -> str:
    """What an entry is made of: its messages, its words and its replies.

    The key a reading is kept under. The date, the addressee and the
    kind of recording are left out: they come from the model's reading
    of the room, and a new reading of the room is not a new entry.
    """
    return json.dumps([entry.event_ids, " ".join(entry.body.split()),
                       [[who, " ".join(text.split())] for who, text in entry.comments]],
                      ensure_ascii=False)


# What stands in for each value a card prompt is built from when its
# skeleton is hashed (`current_template`).
_VALUE = "<value>"


def current_template(entry) -> str:
    """A short hash of the card prompt's skeleton for this entry.

    The prompt with every value that varies (the entry, the vocabulary,
    the family, the language) replaced by a placeholder, as notes name
    theirs (`capture_prompt_template` in the archivist). A change to the
    prompt's wording changes it; a new topic or a new person does not.
    A short entry and a long one take different branches of the prompt
    (`_reading_fields`), so each has its own.
    """
    fields = _reading_fields(entry, _VALUE)
    return transcripts.fingerprint(_EXTRACT_PROMPT.format(
        vocabulary=_VALUE, people=_VALUE, entry=_VALUE, language=_VALUE, **fields))


def report_unknown(entry_id: str, extraction) -> None:
    """Name each household name no declared person goes by: it stays off the card."""
    for name in extraction.unknown:
        err(f"  {entry_id}: no declared person goes by {name!r}, left off the card")
