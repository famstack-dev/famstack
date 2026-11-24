"""_own_calendar: a person's own calendar, for `stack memory list`.

    keeper = keeper_of(vault, actor="homer", audience=frozenset({"homer"}), shared_bucket="family")
    own = OwnCalendar.of(vault, keeper, language="en")   # homer/lists/dates-homer.md
    own.start                                            # the text that makes it, "" when it exists
    shown = shown_to(vault, audience, language="en", shared_bucket="family")

A date a person asks for just for themselves goes on their own calendar,
in their own bucket (`own_calendar_path`). Only that person
reads their own bucket (`stack.cards.audience`), so their pages show the
date next to the family's dates and nobody else's pages do. The command's
`--private` is the way in: `list.py` hands the add, tick, untick or remove
to `OwnCalendar` and writes it the way it writes any list.

SAFETY: the family's calendar is never read or written on this path. A
write that fails saves nothing, and is never turned into a write to the
shared list, because a private date there reaches everyone. Dates already
on the shared list stay there: nothing moves on its own.

Debt (2026-11-23, known since the own calendar was added): the own calendar is found by its path,
which comes from the household language (`dates-homer`, `termine-homer`).
After a change of `[core] language` the old one is not found, and a new
one is made with the next private date. Trigger: the first install that
changes its language after private dates exist; then find it by
`kind: calendar` in the person's bucket.

The underscore keeps the CLI loader from registering this module as a
command.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from stack.cards.audience import readable
from stack.lists import (
    CALENDAR,
    FamilyList,
    ListError,
    calendar_slug,
    calendar_title,
    list_path,
    new_list,
    read_list,
)
from stack.people import Person, load_people

__all__ = ["NotKept", "OwnCalendar", "Shown", "keeper_of", "own_calendar_path", "shown_to"]


class NotKept(ValueError):
    """A private date that was not saved, with the reason the caller is told."""


# ── Where it is ────────────────────────────────────────────────────────────

# A person's own calendar: the dates they keep for themselves, in their own
# bucket, which only they read (`stack.cards.audience`). Its slug names the
# person as well as the calendar ("dates-homer"), because a list's slug is
# its name across the vault and its card id: the family calendar's slug in
# a second bucket would make "the calendar" name two lists in that person's
# own chat, and give two cards one id.
_OWN_CALENDAR_TITLE = {"de": "{title} von {name}", "en": "{name}'s {title}"}
_OWN_CALENDAR_DESCRIPTION = {
    "de": "Termine nur für {name}, für niemanden sonst sichtbar",
    "en": "Dates for {name} alone, shown to nobody else",
}


def own_calendar_path(person: str, language: str) -> str:
    """The vault path of `person`'s own calendar, in their own bucket.

    >>> own_calendar_path("homer", "en")
    'homer/lists/dates-homer.md'
    """
    return list_path(f"{calendar_slug(language)}-{person}", bucket=person)


def own_calendar(name: str, *, path: str, language: str) -> str:
    """The text of a new, empty own calendar at `path` for the person called `name`."""
    code = (language or "").strip().lower()[:2]
    code = code if code in _OWN_CALENDAR_TITLE else "en"
    return new_list(_OWN_CALENDAR_TITLE[code].format(name=name, title=calendar_title(language)),
                    path=path, description=_OWN_CALENDAR_DESCRIPTION[code].format(name=name),
                    kind=CALENDAR)


# ── Who keeps a calendar of their own ─────────────────────────────────────

def keeper_of(vault: Path, *, actor: str, audience: frozenset[str] | None,
              shared_bucket: str) -> Person:
    """The declared person `actor` names, whose own calendar is written; or NotKept.

    SAFETY: the actor names the bucket that is written, so the actor must be
    a person of the family (never the shared bucket or a made-up name), and,
    with an audience, one of the people the answer is for: in Bart's chat
    nobody writes into Homer's folder.
    """
    known = load_people(vault, shared_bucket) or []
    person = next((p for p in known if p.id == actor), None)
    if person is None:
        raise NotKept(f"{actor!r} is not a person of the family, and only a person "
                      "keeps dates of their own")
    if audience is not None and person.id not in audience:
        raise NotKept(f"{person.first_name} is not in this conversation, so no date goes "
                      "on their own calendar from here")
    return person


# ── The calendar itself ───────────────────────────────────────────────────

@dataclass(frozen=True)
class OwnCalendar:
    """One person's own calendar: where it is, what it holds, how to start it.

        own = OwnCalendar.of(vault, homer, language="en")
        own.path      # "homer/lists/dates-homer.md"
        own.list      # its entries, empty until the first date
        own.start     # the text a first date starts it with; "" once it exists
    """

    path: str
    list: FamilyList
    start: str

    @classmethod
    def of(cls, vault: Path, person: Person, *, language: str) -> "OwnCalendar":
        """`person`'s own calendar as the vault holds it, or the one their first
        date makes. Raises NotKept when the file is there and is not a list."""
        path = own_calendar_path(person.id, language)
        if (vault / path).exists():
            try:
                return cls(path, _read(vault, path), "")
            except _Unreadable as e:
                raise NotKept(f"the date was not saved: {e}") from e
        start = own_calendar(person.first_name, path=path, language=language)
        return cls(path, read_list(start, path=path), start)


class _Unreadable(ValueError):
    """An own calendar file that is there and is not a list; says which and why."""


def _read(vault: Path, path: str) -> FamilyList:
    """The list at `path` in `vault`, or `_Unreadable` naming the file, so a
    broken file is named to the person instead of passed over."""
    try:
        return read_list((vault / path).read_text(encoding="utf-8"), path=path)
    except (ListError, OSError, UnicodeDecodeError) as e:
        raise _Unreadable(f"{path} is not a list ({e}); fix or remove the file in Forgejo") from e


# ── Shown next to the family's calendar ───────────────────────────────────

@dataclass(frozen=True)
class Shown:
    """What `show calendar` adds for one listener: their own calendar, or the
    hint that it does not read. Both empty when there is nothing to add."""

    list: FamilyList | None = None
    path: str = ""
    hint: str = ""


def shown_to(vault: Path, audience: frozenset[str] | None, *, language: str,
             shared_bucket: str) -> Shown:
    """The own calendar of the one person the answer is for.

    Only for an audience of exactly one: the admin's view (no audience) and
    a room with others in it never show a person's own dates. A file that is
    there and does not read comes back as `hint`, so the person hears why
    their dates are missing.
    """
    if audience is None or len(audience) != 1:
        return Shown()
    [person] = audience
    path = own_calendar_path(person, language)
    # SAFETY: the rule decides, not the path's shape alone.
    if not readable(path, audience, shared_bucket=shared_bucket) or not (vault / path).exists():
        return Shown()
    try:
        return Shown(_read(vault, path), path)
    except _Unreadable as e:
        return Shown(path=path, hint=f"Your own dates are not shown: {e}")
