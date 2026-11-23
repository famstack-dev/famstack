"""The people that a capture's card names: each name that the model gave, placed against the known people.

    people = People.of(vault, pages=brain, account_names="Alex,Sam,Jo")
    placed = people.place(["Alex", "Mr. Gray"], said="Jo came along.", sender="Sam")
    placed.names        # ['Alex', 'Jo']: what the card is about
    placed.unplaced     # ['Mr. Gray']: nobody goes by it

A model that is asked who a note is about answers with names.
A card's `persons` holds person ids (ADR-017 §4).
The About line of the card links the card of each person.
The writer therefore places every name that the model gave.
A name has one of three outcomes:

    the name             the people know it as           on the card
    "Mr. Doe"            an alias of alex                persons: [alex], a link
    "Jo"                 a name in FAMILY_NAMES only     a plain name in the About line
    "Mr. Gray"           nobody                          nothing, the text keeps it

A name that nobody goes by stays off the card.
A misheard name or a stranger's name then never becomes a person.
The person pages and the search do not grow a "Mr. Gray".
The name stays in the words and the sources of the note, and the writer reports it in its log.

The second outcome applies when no person file exists yet.
The accounts then name the people (`FAMILY_NAMES` from `users.toml`).
A card names them without an id.

The names come from the same files that the diary reads (`stack.people.names`):

- the person files with their aliases,
- the person pages of the wiki with the spellings that documents used (`<id>/about.md` in the copy of the wiki),
- `FAMILY_NAMES`, before any person file exists.

The model can leave a known person out of its list although its title, description or summary names them.
That person still joins the card, as on a diary entry (`stack.people.names.named_in`).
Only known names match, and only when they are written as names.

- "Jo came along." gives Jo.
- "Will it rain in May?" gives nobody unless someone is called Will or May.

`place` reads that text only when the caller passes it.
The capture passes it for a note, never for a bookmark or an email.

When no name is left, the card is about the sender.
A note from alex that names only Mr. Gray is alex's note ("Mr. Gray called about the plant").
The sender always counts as known, also when no person file and no `FAMILY_NAMES` exist.

The module reads files and applies string rules.
It uses only the standard library, like `stack.filing.vocabulary`.
The capture of the archivist and `stack memory capture` call it before the mirror writes the card.
Documents and email keep their own rule (`stack.cards.links.About`, names kept as written).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..people import Person, load_people
from ..people.names import card_people, declared_as_persons, load_persons_from_vault, merge_names, named_in
from ..vault import DEFAULT_SHARED_BUCKET

__all__ = ["People", "Placed"]


@dataclass(frozen=True)
class Placed:
    """The names a card is about, and the names that nobody goes by.

        placed = People({"alex": "Alex"}).place(["Alex", "Mr. Gray"])
        placed.names, placed.unplaced          # (['Alex'], ['Mr. Gray'])
    """

    names: list[str] = field(default_factory=list)
    unplaced: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class People:
    """The known people as a capture places names.

    The mapping holds every name that a person goes by, lowercased, and the name that a card writes for them.
    Build it with `People.of`.

        people = People({"alex": "Alex", "mr. doe": "Alex", "jo": "Jo"})
        people.place(["Mr. Doe", "Mr. Gray"]).names    # ['Alex']
    """

    names: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, vault: Optional[Path], *, pages: Optional[Path] = None, account_names: str = "",
           shared_bucket: str = DEFAULT_SHARED_BUCKET) -> "People":
        """The people of the memory in `vault`.

        With person files (`<vault>/<shared_bucket>/people/<id>.md`), each declared person is known by every name they go by.
        The spellings of their wiki page in `pages` are added.
        `pages` is the copy of the memory for the wiki (`BRAIN_REPO_DIR`), where `<id>/about.md` is generated.
        Without person files, the people are the names in `account_names` (comma-separated, `FAMILY_NAMES`)
        and the person pages of the wiki.
        With no vault (`None`), the function reads no person file.
        The function leaves out a file that does not read, as every reader of these files does.

            People.of(vault, pages=brain).place(["Mr. Doe"]).names            # ['Alex']
            People.of(None, account_names="Alex,Jo").place(["jo"]).names      # ['Jo']
        """
        seen = load_persons_from_vault(pages, shared_bucket) if pages is not None else []
        declared = load_people(vault, shared_bucket) if vault is not None else None
        if declared is None:
            return cls(card_people(seen, merge_names(account_names, [])))
        return cls(card_people(declared_as_persons(declared, seen), _names_of(declared)))

    def place(self, given: Iterable[str] | str | None, *, said: str = "",
              sender: str = "") -> Placed:
        """The names a card is about, each once, in this order.

        1. Each name of `given` that a known person goes by.
        2. Each known person that `said` names.
        3. The `sender`, when no name is left.

        Every name of `given` that nobody goes by is in `unplaced`.

        `given` is the list of the model.
        A string or None from a careless answer counts as one name or none.
        `said` is the title, the description and the summary of a note that the model wrote.
        Leave it empty for a bookmark or an email.
        `sender` is the name of the person who sent the capture ("Sam").
        Leave it empty when the sender is not a person, for example a bot that posted an attachment.

            people = People({"alex": "Alex", "jo": "Jo"})
            people.place(["alex"], said="Jo came along.").names       # ['Alex', 'Jo']
            people.place(["Mr. Gray"], sender="Alex").names           # ['Alex']
            people.place([], said="Will it rain in May?", sender="Jo").names  # ['Jo']
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

    def place_answer(self, answer: dict, *, note: bool, sender: str = "", said: str = "") -> Placed:
        """`place` for the answer of a model about a capture.

        The answer's `persons` are placed.
        For a `note`, the people that its title, description and summary name are placed too,
        and the people that `said` names: words of the answer the card does not keep.

            people.place_answer({"title": "Jo came along", "persons": ["Alex"]},
                                note=True, sender="Sam").names           # ['Alex', 'Jo']
            people.place_answer({"title": "Jo came along", "persons": []},
                                note=False, sender="Sam").names          # ['Sam']
        """
        words = " ".join([*(str(answer.get(key) or "") for key in _SAID), said]) if note else ""
        return self.place(answer.get("persons"), said=words.strip(), sender=sender)


# ── Reading the answer ───────────────────────────────────────────────────

# The parts of a model's answer that say in words what a note is about.
_SAID = ("title", "description", "summary")


def _names(given: Iterable[str] | str | None) -> list[str]:
    """`given` as names, each with its spaces folded, empty ones left out."""
    values = [given] if isinstance(given, str) else list(given or [])
    return [n for n in (" ".join(str(v).split()) for v in values if isinstance(v, str)) if n]


def _names_of(declared: list[Person]) -> list[str]:
    """Every name that the declared people go by, in the order of their files."""
    return [name for person in declared for name in person.all_names()]
