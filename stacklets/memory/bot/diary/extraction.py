"""What a model reads out of a diary entry, checked before it reaches a card.

    extraction = extraction_from(raw, topics=Vocabulary.of("family", vault, seed=SEED),
                                 people={"maggie": "Maggie"}, declared=declared)

The model answers JSON; `extraction_from` keeps what fits the card's
schema: the people the household knows, the topics of the vocabulary, a
day in ISO form, facts that say more than the kind of entry. What does
not fit is dropped or listed in `unknown`, never guessed into the card.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from stack.cards.links import About
from stack.people import Person
from stack.filing.topic_list import chosen
from stack.tags import card_tags
from stack.people.names import named_in

# ── What a model reads out of an entry ────────────────────────────────


@dataclass(frozen=True)
class Extraction:
    """The machine half of a card: what a model read out of the entry.

    Everything here is generated and may be wrong, which is why it all
    lands on a card a person can correct. Empty is valid: an entry the
    model could not read still becomes a record, with a plain title.
    `quotes` are passages the model copied; they are checked against the
    entry's words again whenever a page renders them.
    """

    title: str = ""
    description: str = ""
    persons: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    facts: list[str] = field(default_factory=list)
    quotes: list[str] = field(default_factory=list)
    model: str = ""
    # A day a family member's correction states, as YYYY-MM-DD. Honoured
    # only for an entry that has corrections (see `to_card`).
    date: str = ""
    # Whom the entry is spoken to, as a correction states it. Only
    # `correct` uses it; a first reading takes the addressee from the room.
    addressee: str = ""
    # Household names the model gave that no declared person goes by:
    # kept off `persons`, for the caller to report.
    unknown: list[str] = field(default_factory=list)
    # The prompt template the reading was made under (a hash of the
    # prompt's wording, not of the entry or the vocabulary it offers).
    template: str = ""


_MAX_QUOTES = 3
_LIST_MARKER = re.compile(r"^(?:\s*[-*\u2022]\s+)+")


def _strings(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [" ".join(v.split()) for v in value if isinstance(v, str) and v.strip()]


# Labels that say what kind of thing a fact is rather than whom or what it
# is about. Models reach for them when asked for "Label: value", and the
# result restates the summary, the date or the people as a form.
_KIND_LABELS = {"action", "actions", "activity", "detail", "details", "event",
                "events", "date", "year", "time", "speaker", "recipient",
                "person", "persons", "people", "note"}


def _kind_only(fact: str) -> bool:
    label, sep, _ = fact.partition(":")
    return bool(sep) and label.strip().lower() in _KIND_LABELS


def _addressee(value, people: "dict[str, str]") -> str:
    """Whom a correction says the entry was for.

    A household member's name resolves to its canonical form; anyone else
    ("the kids", "Grandpa") is kept as the family wrote it. Nothing, or a
    model's null, is "".
    """
    if not isinstance(value, str):
        return ""
    words = " ".join(value.split())
    if not words or words.lower() in ("null", "none"):
        return ""
    return people.get(words.lower(), words)


def _iso_day(value) -> str:
    """`value` if it is a YYYY-MM-DD day, else ""."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        return ""
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return ""


def extraction_from(raw, *, topics, people: "dict[str, str]",
                    declared: list[Person] | None, model: str) -> Extraction:
    """A model's answer about one entry, held to the household's vocabulary.

    `people` maps every known name, lowercased, to its canonical form
    (a person page's canonical name and synonyms). A name outside it is
    dropped: a mishearing must not become a family member. Everyone the
    title, the description or the summary names is a person the card is
    about, the people it could be included ("Marge and Bart or Lisa"),
    whatever the model listed. `persons` holds the id of the declared
    person (`declared`, `stack.people`) each canonical name means
    (ADR-017 §4); a name no declared person goes by stays off the card and
    is listed in `unknown`. `topics` is the vocabulary of the card's
    place (`stack.filing.vocabulary.Vocabulary`): a topic the model offers
    is read as a capture reads the topics it chose (`stack.filing.topic_list.chosen`),
    by id, name, a topic card's title, a renamed topic's former id, then
    an ontology synonym, in any language. A document type or a word that
    names no topic is dropped, never kept as a keyword. Tags are the
    topics, each by its id like on every other card (`stack.tags.card_tags`).
    `persons` says who.
    """
    if not isinstance(raw, dict):
        return Extraction(model=model)

    def text(key: str) -> str:
        value = raw.get(key)
        return " ".join(value.split()) if isinstance(value, str) else ""

    named = named_in(" ".join(text(k) for k in ("title", "description", "summary")), people)
    about = About.of(list(dict.fromkeys([*(people[n.lower()] for n in _strings(raw.get("persons"))
                                if n.lower() in people), *named])), declared)
    picked = chosen(topics, _strings(raw.get("topics")))

    return Extraction(
        title=text("title"),
        description=text("description"),
        persons=about.ids,
        tags=card_tags(picked, topics.ontology),
        summary=text("summary"),
        facts=[f for f in (_LIST_MARKER.sub("", f) for f in _strings(raw.get("facts")))
               if f and not _kind_only(f)],
        quotes=_strings(raw.get("quotes"))[:_MAX_QUOTES],
        model=model,
        date=_iso_day(raw.get("date")),
        addressee=_addressee(raw.get("addressee"), people),
        unknown=about.unknown,
    )
