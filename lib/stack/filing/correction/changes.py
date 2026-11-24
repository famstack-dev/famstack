"""changes: a card's fields after a correction, with one guard for tags and people.

    corrected = named_changes(
        {"title": "Fuel receipt", "tags": ["vehicle", "shopping"], "persons": ["Homer"]},
        {"title": "Camper van fuel, March", "tags": {"add": ["travel"], "remove": ["shopping", "vehicle"]},
         "persons": {"remove": ["Homer"]}},
        said="The title is Camper van fuel, March. The tag shopping is wrong, it is travel.",
        people=[Person("homer", "Homer Simpson")])
    corrected.fields
    # {'title': 'Camper van fuel, March', 'tags': ['vehicle', 'travel'], 'persons': ['Homer']}
    corrected.changed     # ('title', 'tags')

The model reads a correction as the card conversation (`conversation`).
It answers what the correction changes. This module applies that answer
to the card's fields.

Most corrections are about a card's content, for example a wrong number
in a transcript. The fields therefore get one guard only. The guard keeps
a tag that the correction does not name, because a model can drop values
that nobody named. A tag that a person set is then lost without notice.
The people follow the answer only when the correction is about people.
A typical correction about people is "only me" on a card with too many
people.

| Field | After the correction |
|---|---|
| title, date | The model's answer. A date counts only as `YYYY-MM-DD`. No answer keeps the card's value. |
| tags | The model's additions. A tag goes only when the correction names it, by its id or by a name in any language (`tag_names`). |
| persons | The model's additions and its explicit removals (`{"remove": [...]}`), when the correction names a person or its writer. Otherwise the card's people. |

A correction names its writer with `me` or `mine`, or with one of their
German forms (`_WRITER`). A whole list of people in the answer only adds,
because a person goes only by an explicit remove. A person that the person
files do not hold is not added. `Corrected.unknown` names that person,
because the reply tells the writer. An answer that is not an object
changes nothing.

The answer can give a list field as changes (`{"add": [...], "remove":
[...]}`) or as a whole list. A whole list is compared with the card's
list. The module is pure: it has no I/O and calls no model.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from stack.people import Person, resolve

__all__ = ["Corrected", "named_changes"]

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# The words by which the writer of a correction names themselves.
_WRITER = re.compile(r"(?<!\w)(?:me|mich|ich|mine|meins)(?!\w)", re.IGNORECASE)


@dataclass(frozen=True)
class Corrected:
    """A card's fields after a correction.

    ``fields`` holds the card's title, tags, persons and date after the
    correction. ``changed`` names the fields that differ from the filed
    card. ``unknown`` names the people that the answer added and that the
    person files do not hold.

        Corrected({"title": "BBQ", "tags": ["grill"], "persons": ["Marge"]}, ("tags",))
    """

    fields: dict
    changed: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()


def named_changes(filed: Mapping, answer: object, *, said: str,
                  people: Sequence[Person] = (),
                  tag_names: Mapping[str, Sequence[str]] | None = None) -> Corrected:
    """The card `filed` with the changes of `answer`, after the guard.

    No tag goes that `said` does not name. The people change only when
    `said` is about people.

    ``filed`` holds the card's ``title``, ``tags``, ``persons`` and
    ``date``. Its other keys pass through unchanged. ``answer`` is the
    model's answer to the correction. ``said`` is the text of the
    corrections without their writers (`stack.cards.events.text_of`).

    ``people`` are the person files. A person that the answer adds must be
    one of them. ``tag_names`` gives the other names of each tag, for
    example the name of the topic `vehicle` in each language.

        named_changes({"title": "BBQ", "tags": ["grill"], "persons": ["Marge"]},
                      {"title": "Grill party", "persons": {"remove": ["Marge"]},
                       "tags": {"add": ["party"]}},
                      said="it was a party").fields
        # {'title': 'Grill party', 'tags': ['grill', 'party'], 'persons': ['Marge']}
        # Marge stays, because the correction names no person.
    """
    fields = dict(filed)
    if not isinstance(answer, Mapping):
        return Corrected(fields)
    fields["title"] = _title(str(filed.get("title") or ""), answer.get("title"))
    fields["tags"] = _tags(_strings(filed.get("tags")), answer.get("tags"), said, tag_names or {})
    fields["persons"], unknown = _persons(_strings(filed.get("persons")), answer.get("persons"),
                                          said, people)
    fields["date"] = _date(filed.get("date"), answer.get("date"))
    changed = tuple(k for k in ("title", "tags", "persons", "date")
                    if _differs(filed.get(k), fields[k]))
    if "date" not in filed and fields["date"] is None:
        del fields["date"]
    return Corrected(fields, changed, unknown)


# ── The fields ────────────────────────────────────────────────────────


def _title(title: str, answered: object) -> str:
    """The answer's title, else the card's."""
    return answered.strip() if isinstance(answered, str) and answered.strip() else title


def _date(date: object, answered: object) -> object:
    """The answer's date when it is one (`YYYY-MM-DD`), else the card's."""
    return answered if isinstance(answered, str) and _ISO_DATE.match(answered) else date


def _tags(tags: list[str], answered: object, said: str,
          tag_names: Mapping[str, Sequence[str]]) -> list[str]:
    """The card's tags with the answer's additions, without each removed
    tag that the correction names.

    A card's tag counts as answered when one of its names is in the
    answer. An added tag stays as the model wrote it. The write maps a
    topic's name to its id (`stack.filing.vocabulary.Vocabulary.topics`)."""
    def names(tag: str) -> list[str]:
        return [tag, *tag_names.get(tag, ())]

    def among(values: Sequence[str], tag: str) -> bool:
        return any(_holds(values, n) for n in names(tag))

    add, remove = _list_change(tags, answered)
    if isinstance(answered, list):
        add = [a for a in add if not any(_holds(names(t), a) for t in tags)]
        remove = [t for t in tags if not among(_strings(answered), t)]
    kept = [t for t in tags
            if not (among(remove, t) and any(_names(said, n) for n in names(t)))]
    added: list[str] = []
    for tag in add:
        if not any(_holds(names(k), tag) for k in kept + added):
            added.append(tag)
    return kept + added


def _persons(persons: list[str], answered: object, said: str,
             people: Sequence[Person]) -> tuple[list[str], tuple[str, ...]]:
    """The card's people after the answer, and the added names that the
    person files do not hold.

    The people change only when the correction names a person or its
    writer. A name that the person files do not hold is not added.

        _persons(["Homer", "Marge", "Bart", "Lisa"],
                 {"remove": ["Marge", "Bart", "Lisa"]}, "only me", SIMPSONS)
        # (['Homer'], ())
        _persons(["Homer"], ["Marge"], "Marge was there too", SIMPSONS)
        # (['Homer', 'Marge'], ())
    """
    add, remove = _list_change(persons, answered)
    if isinstance(answered, list):
        remove = []  # A whole list only adds: a person goes only by an explicit remove.
    if not _about_people(said, people, add + remove):
        return persons, ()
    kept = [p for p in persons if not _same_person_in(remove, p, people)]
    added: list[str] = []
    unknown: list[str] = []
    for name in add:
        person = resolve(name, list(people)) if people else None
        if person is None:
            unknown.append(name)
        elif not _same_person_in(kept + added, person.first_name, people):
            added.append(person.first_name)
    return kept + added, tuple(unknown)


# ── Reading the answer ────────────────────────────────────────────────


def _list_change(current: list[str], answered: object) -> tuple[list[str], list[str]]:
    """What the answer adds to and removes from a list field: its `add`
    and `remove`, or a whole list's difference to `current`."""
    if isinstance(answered, Mapping):
        return _strings(answered.get("add")), _strings(answered.get("remove"))
    if isinstance(answered, list):
        whole = _strings(answered)
        return ([a for a in whole if not _holds(current, a)],
                [c for c in current if not _holds(whole, c)])
    return [], []


def _strings(value: object) -> list[str]:
    """A list field's values as strings. A single string is a list of one."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if isinstance(v, (str, int)) and str(v).strip()]


# ── Whether the correction names it ───────────────────────────────────


def _names(said: str, name: str) -> bool:
    """Whether `name` stands in `said` as a word or phrase of its own.

    Case does not matter. The dashes and underscores of a tag id count as
    spaces."""
    return bool(re.sub(r"[-_]+", " ", name).strip()) and _phrase(name).search(said) is not None


def _phrase(name: str) -> re.Pattern[str]:
    """A pattern that finds `name` as a phrase of its own, in any case.

    Spaces, dashes and underscores between its words all match."""
    words = re.sub(r"[-_]+", " ", name).split()
    pattern = r"[\s\-_]+".join(re.escape(w) for w in words)
    return re.compile(rf"(?<!\w){pattern}(?!\w)", re.IGNORECASE)


def _holds(values: Sequence[str], value: str) -> bool:
    """Whether `values` holds `value`, in any case and with spaces, dashes or
    underscores between its words."""
    return _spaced(value) in {_spaced(v) for v in values}


def _spaced(value: str) -> str:
    return " ".join(re.sub(r"[-_]+", " ", value).casefold().split())


def _about_people(said: str, people: Sequence[Person], answered: Sequence[str]) -> bool:
    """Whether the correction is about people.

    It is when it names its writer or a person of the person files. It is
    also when it names a person that the answer adds or removes ("Lenny
    was there too")."""
    return (_WRITER.search(said) is not None
            or any(_person_named(said, p.id, people) for p in people)
            or any(_names(said, n) for n in answered))


def _person_named(said: str, name: str, people: Sequence[Person]) -> bool:
    """Whether the correction names the person `name` is, by any of their names."""
    person = resolve(name, list(people)) if people else None
    names = person.all_names() if person else [name]
    return any(_names(said, n) for n in (*names, *([person.id] if person else [])))


def _same_person_in(names: Sequence[str], name: str, people: Sequence[Person]) -> bool:
    """Whether `names` holds the person `name` is, under any of their names."""
    person = resolve(name, list(people)) if people else None
    if person is None:
        return _holds(names, name)
    return any((resolve(n, list(people)) or Person("", "")).id == person.id for n in names)


def _differs(before: object, after: object) -> bool:
    if isinstance(before, list) or isinstance(after, list):
        return sorted(_strings(before)) != sorted(_strings(after))
    return (before or None) != (after or None)
