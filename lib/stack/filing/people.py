"""The people a capture's card names: each name placed against the household's people.

    people = People.of(vault, pages=brain, family_names="Homer,Marge,Bart,Lisa")
    placed = people.place(["Homer", "Mr. Burns"], said="Lisa came along.", sender="Marge")
    placed.names        # ['Homer', 'Lisa']: what the card is about
    placed.unplaced     # ['Mr. Burns']: nobody in the household goes by it

A model asked who a note is about answers with names. A card's `persons`
holds person ids (ADR-017 §4), and its About line links each person's card,
so a writer has to place every name the model gave. A name has one of three
outcomes:

    the name             the household knows it as      on the card
    "Mr. Simpson"        an alias of Homer's file        persons: [homer], a link
    "Lisa"               a name in FAMILY_NAMES only     a plain name in the About line
    "Mr. Burns"          nobody                          nothing; the text keeps it

A name nobody goes by stays off the card: a misheard or a stranger's name
never becomes a person, and the person pages and the search do not grow a
"Mr. Burns". It stays in the note's words and sources, and the writer
reports it in its log. The second outcome is a household that has no
person files yet: its people are known by the names the accounts give
(`FAMILY_NAMES`, from `users.toml`), and a card names them without an id.

The household's names come from the same files the diary reads
(`stack.household_people`): the person files with their aliases, the
wiki's person pages with the spellings documents used
(`<id>/about.md` in the wiki's copy), and `FAMILY_NAMES` before any person file
exists. A known person the model's title, description or summary names
joins the card although the model left them out of its list, as on a
diary entry (`stack.household_people.named_in`): only the household's own
names match, written as names, so "Lisa came along." gives Lisa and "Will
it rain in May?" gives nobody unless someone is called Will or May.
`place` reads that text only when the caller passes it: the capture passes
it for a note, never for a bookmark or an email.

When no name is left, the card is about the one who sent it: a note from
Homer that names only Mr. Burns is Homer's ("Mr. Burns called about the
plant"). The sender always counts as known, also in a household that has
no person files and no `FAMILY_NAMES`.

Pure file reads and string rules, stdlib only, like
`stack.filing.vocabulary`: the archivist's capture and `stack memory
capture` call it before the mirror writes the card. Documents and email
keep their own rule (`stack.cards.links.About`, names kept as written).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..household_people import card_people, declared_as_persons, household, load_persons_from_vault, named_in
from ..people import Person, load_people
from ..vault import DEFAULT_SHARED_BUCKET

__all__ = ["People", "Placed"]


@dataclass(frozen=True)
class Placed:
    """The names a card is about, and the names nobody in the household goes by.

        placed = People({"homer": "Homer"}).place(["Homer", "Mr. Burns"])
        placed.names, placed.unplaced          # (['Homer'], ['Mr. Burns'])
    """

    names: list[str] = field(default_factory=list)
    unplaced: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class People:
    """The household's people as a capture places names: every name they
    go by, lowercased, to the name a card writes for them. Build it with
    `People.of`.

        people = People({"homer": "Homer", "mr. simpson": "Homer", "lisa": "Lisa"})
        people.place(["Mr. Simpson", "Mr. Burns"]).names    # ['Homer']
    """

    names: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, vault: Optional[Path], *, pages: Optional[Path] = None, family_names: str = "",
           shared_bucket: str = DEFAULT_SHARED_BUCKET) -> "People":
        """The people of the household whose memory is `vault`.

        With person files (`<vault>/<shared_bucket>/people/<id>.md`): each
        declared person by every name they go by, with the spellings of
        their wiki page in `pages` (the wiki's copy of the memory,
        `BRAIN_REPO_DIR`, where `<id>/about.md` is generated). Without
        them: the names in `family_names` (comma-separated, `FAMILY_NAMES`)
        and the wiki's person pages. No vault (`None`) reads no person file.
        A file that does not read is left out, as everywhere they are read.

            People.of(vault, pages=brain).place(["Mr. Simpson"]).names       # ['Homer']
            People.of(None, family_names="Homer,Lisa").place(["lisa"]).names  # ['Lisa']
        """
        seen = load_persons_from_vault(pages, shared_bucket) if pages is not None else []
        declared = load_people(vault, shared_bucket) if vault is not None else None
        if declared is None:
            return cls(card_people(seen, household(family_names, [])))
        return cls(card_people(declared_as_persons(declared, seen), _names_of(declared)))

    def place(self, given: Iterable[str] | str | None, *, said: str = "",
              sender: str = "") -> Placed:
        """The names a card is about: each of `given` the household knows,
        then each known person `said` names, once, in that order; else the
        `sender`. Every name of `given` nobody goes by is in `unplaced`.

        `given` is the model's list (a string or None from a careless
        answer is read as one name or none). `said` is the model's title,
        description and summary of a note; leave it empty for a bookmark
        or an email. `sender` is the name of the person who sent the
        capture ("Marge"); leave it empty where the sender is not a person
        of the household (a bot that posted an attachment).

            people = People({"homer": "Homer", "lisa": "Lisa"})
            people.place(["homer"], said="Lisa came along.").names       # ['Homer', 'Lisa']
            people.place(["Mr. Burns"], sender="Homer").names            # ['Homer']
            people.place([], said="Will it rain in May?", sender="Lisa").names  # ['Lisa']
        """
        names, unplaced = [], []
        for name in _names(given):
            if (known := self.names.get(name.lower())) is not None:
                names.append(known)
            elif name not in unplaced:
                unplaced.append(name)
        names.extend(named_in(said, dict(self.names)) if said else [])
        if not names and sender:
            names.append(self.names.get(sender.lower(), sender))
        return Placed(list(dict.fromkeys(names)), unplaced)

    def place_answer(self, answer: dict, *, note: bool, sender: str = "") -> Placed:
        """`place` for a model's answer about a capture: its `persons`, and
        for a `note` the people its title, description and summary name.

            people.place_answer({"title": "Lisa came along", "persons": ["Homer"]},
                                note=True, sender="Marge").names         # ['Homer', 'Lisa']
            people.place_answer({"title": "Lisa came along", "persons": []},
                                note=False, sender="Marge").names        # ['Marge']
        """
        said = " ".join(str(answer.get(key) or "") for key in _SAID) if note else ""
        return self.place(answer.get("persons"), said=said, sender=sender)


# ── Reading the answer ───────────────────────────────────────────────────

# The parts of a model's answer that say in words what a note is about.
_SAID = ("title", "description", "summary")


def _names(given: Iterable[str] | str | None) -> list[str]:
    """`given` as names, each with its spaces folded, empty ones left out."""
    values = [given] if isinstance(given, str) else list(given or [])
    return [n for n in (" ".join(str(v).split()) for v in values if isinstance(v, str)) if n]


def _names_of(declared: list[Person]) -> list[str]:
    """Every name the declared people go by, in the order of their files."""
    return [name for person in declared for name in person.all_names()]
