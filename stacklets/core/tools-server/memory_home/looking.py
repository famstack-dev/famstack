"""Who looks at a memory page, and which of the memory's views is theirs.

The device gate says who is looking (`devices.who`): a person's phone, a
household device, or the Mac's own address. That decides the memory's
view "My memory" is drawn from, and which person's topics the menu lists:

    looking = Looking("person", "marge")
    view_of(looking)                        'me'
    reads = mine(reads, looking)            the reads that view is asked with
    await own_topics(reads, looking)        [{"id": "topic-garden", ...}]
    device_person(looking, names)           ('marge', 'Marge')

**The Mac's "My memory" is the admin's, as their phone shows it.** The
Mac's address reads every card, but its "My memory" is the admin's page,
so it asks the memory for `me` with the admin's audience (`mine`): a card
in another person's own folder that names the admin is not on it, as it
is not on the admin's phone. Every other page on the Mac keeps reading
everything.

A household device is no person: its "My memory" is the family's page and
its menu has no topics.

Pure but for `own_topics`, which reads the memory.
"""

from __future__ import annotations

from dataclasses import dataclass

import family_view
from memory_ui.reads import Reads

__all__ = ["Looking", "view_of", "mine", "own_topics", "device_person"]


@dataclass(frozen=True)
class Looking:
    """Who is looking, as the device gate found it (`devices.Who`): `kind`
    is "person" (`person` names them), "household", "mac" or "nobody";
    `family` is what `stack family` shows, None when it is not known.

        Looking("person", "marge")
        Looking("mac", family={"name": "Simpson", "people": [...]})
    """

    kind: str
    person: str = ""
    family: dict | None = None


def view_of(looking: Looking) -> str:
    """The memory's view that "My memory" is for `looking`: `me` for a
    person and for the Mac with a known admin, else `family`.

        >>> view_of(Looking("person", "marge"))
        'me'
        >>> view_of(Looking("mac", family={"people": [{"id": "homer", "role": "admin"}]}))
        'me'
        >>> view_of(Looking("household"))
        'family'
    """
    return "me" if _person_of(looking) else "family"


def mine(reads: Reads, looking: Looking) -> Reads:
    """The reads "My memory" and the menu ask with: the device's own, and
    on the Mac the admin's audience (`Reads.seen_by`), because `me` is the
    page of exactly one person."""
    if looking.kind == "mac" and (person := _person_of(looking)):
        return reads.seen_by(person)
    return reads


async def own_topics(reads: Reads, looking: Looking) -> list[dict]:
    """The topics of the person the device is paired to (the admin's on
    the Mac's address), as their "My memory" answers them; [] for a
    household device, and when the memory does not answer."""
    if view_of(looking) == "family":
        return []
    _, answer = await mine(reads, looking).overview("me")
    return (answer or {}).get("topics", [])


def device_person(looking: Looking, names: dict[str, str]) -> tuple[str, str] | None:
    """The person the device is paired to, or the admin on the Mac's
    address, as (id, first name); None for a household device."""
    person_id = _person_of(looking)
    return (person_id, family_name(person_id, names, looking)) if person_id else None


def family_name(person_id: str, names: dict[str, str], looking: Looking) -> str:
    """The first name of `person_id`: from their person card (`names`),
    else from the family `stack family` shows, else their id as a name."""
    found = next((p for p in (looking.family or {}).get("people", []) if p.get("id") == person_id), {})
    name = names.get(person_id) or str(found.get("name") or "").split(" ")[0]
    return name or person_id.capitalize()


def _person_of(looking: Looking) -> str:
    """The id of the person the device is: its paired person, the admin on
    the Mac's address; "" for any other device."""
    if looking.kind == "person" and looking.person:
        return looking.person
    if looking.kind == "mac" and (admin := family_view.admin_of(looking.family)):
        return str(admin["id"])
    return ""
