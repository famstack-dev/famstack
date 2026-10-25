"""What the card store checks before it writes a card (ADR-019 §4).

The curator's card store is every writer's way into the memory, so it is
the one place a card is checked, whoever wrote it. `CardCheck` is what the
curator gives its store:

    store = CardStore(clone, remote="curator", server_name="simpson",
                      check=CardCheck(shared_bucket="family"))

For each file a write puts, it answers what is wrong, one entry per field,
none when the write may go ahead:

- **A card meets its type's schema.** A markdown file whose frontmatter
  names a `type` is a card, checked against `schemas/cards/<type>.json`
  with the values this instance decides: its declared people, its topics
  (`card_schema.CardSchemas`). A page without a `type` is someone's page
  written by hand; the curator makes it a note and gives it its id.
- **A write changes what a card says, never which card it is.** A card
  written over another keeps its `card_id`, or names the one it had among
  its `former_card_ids`, as a card of a new type does (ADR-016).
- **A sidecar is its card's.** The sidecar beside a note (ADR-018) carries
  the note's `card_id`.
- **The ontology parses.** `ontology.toml` holds the topics a card may
  name; one that does not parse is not written, and while the memory's
  own does not (a hand edit in Forgejo), a card is refused naming it,
  since its topics cannot be checked.

A migration's commit (one with a `Migration:` trailer) is checked for the
last two only: migrations bring old cards into the format in steps.

Each entry is `{"path", "field", "message"}`, with `"allowed"` where the
schema lists what may go there. Runs in the curator's container, which
carries `jsonschema`.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from card_schema import CardSchemas, VaultValues
from stack.cards import card_id_from, former_card_ids
from stack.cards.sources import sidecar_path
from stack.frontmatter import FrontmatterError, parse
from stack.people import load_people
from stack.vault import DEFAULT_SHARED_BUCKET

# What a check reads the memory as it stands with: a path's text, or None.
Reader = Callable[[str], "str | None"]

# Where the memory keeps its topics (`memory.lib.ONTOLOGY_PATH_IN_REPO`).
ONTOLOGY_FILE = "ontology.toml"


def _memory_ontology() -> Callable:
    """The memory's ontology reader (`memory.lib.get_ontology`), taken when
    the check is built, where the curator builds it, not when a request
    comes in."""
    from memory.lib import get_ontology
    return get_ontology


@dataclass
class CardCheck:
    """The checks the card store runs on a write's files; see the module."""

    shared_bucket: str = DEFAULT_SHARED_BUCKET
    schemas: CardSchemas = field(default_factory=CardSchemas.load)
    ontology_of: Callable = field(default_factory=_memory_ontology)

    def __call__(self, puts: Sequence[tuple[str, str]], read: Reader, repo, *,
                 formats: bool = True) -> list[dict]:
        """What is wrong with `puts`, `(path, text)` each, against the memory
        `read` gives and the clone `repo` holds the instance's values in.

        `formats` False leaves the schemas out: a migration brings old cards
        into the format in steps, so one card can be between formats until
        the last of them has run, and what has to meet the format is the end
        of the chain (`test_memory_migration_chain`). Identity is checked
        either way.
        """
        cards = {path: fields for path, text in puts if path.endswith(".md")
                 and (fields := _fields(text)) is not None}
        problems = [_unparsed(path) for path, text in puts
                    if formats and path.endswith(".md") and _fields(text) is None]
        problems += [_ontology_unparsed(error) for path, text in puts
                     if formats and path == ONTOLOGY_FILE and (error := _toml_error(text))]
        if formats and any(fields.get("type") for fields in cards.values()):
            try:
                values = self._values(repo)
            except tomllib.TOMLDecodeError as e:
                return problems + [_ontology_unparsed(e)]
            for path, fields in cards.items():
                if fields.get("type"):
                    problems += [{"path": path, **p} for p in self.schemas.problems(fields, values)]
        for path, fields in cards.items():
            problems += _identity(path, fields, read(path))
        written = dict(puts)
        for path, text in puts:
            if path.endswith(".json") and (card := _card_beside(path, written, read)) is not None:
                problems += _sidecar(path, text, card)
        return problems

    def _values(self, repo) -> VaultValues:
        people = load_people(repo, self.shared_bucket) or []
        return VaultValues([p.id for p in people], self.ontology_of(repo))


def _fields(text: str) -> dict | None:
    """A markdown file's frontmatter; {} for none, None when it does not parse."""
    try:
        return parse(text)
    except FrontmatterError:
        return None


def _unparsed(path: str) -> dict:
    return {"path": path, "field": "frontmatter", "message": "the frontmatter does not parse"}


def _toml_error(text: str) -> tomllib.TOMLDecodeError | None:
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return e
    return None


def _ontology_unparsed(error: tomllib.TOMLDecodeError) -> dict:
    return {"path": ONTOLOGY_FILE, "field": "ontology",
            "message": f"{ONTOLOGY_FILE} does not parse ({error}); correct it in Forgejo, "
                       "then write again"}


def _identity(path: str, fields: dict, before: str | None) -> list[dict]:
    had = card_id_from(_fields(before) or {}) if before is not None else ""
    now = card_id_from(fields)
    if not had or now == had or had in former_card_ids(fields):
        return []
    return [{"path": path, "field": "card_id",
             "message": f"the card is {had}; a change never makes it {now or 'a card without an id'}"}]


def _card_beside(sidecar: str, written: dict[str, str], read: Reader) -> dict | None:
    """The note a sidecar at `sidecar` belongs to, as the write leaves it."""
    card_path = str(PurePosixPath(sidecar).with_suffix(".md"))
    if sidecar_path(card_path) != sidecar:
        return None
    text = written.get(card_path)
    text = text if text is not None else read(card_path)
    return _fields(text) if text is not None else None


def _sidecar(path: str, text: str, card: dict) -> list[dict]:
    try:
        carries = (json.loads(text) or {}).get("card_id")
    except (ValueError, AttributeError):
        return [{"path": path, "field": "card_id", "message": "the sidecar is no JSON object"}]
    if carries == card_id_from(card):
        return []
    return [{"path": path, "field": "card_id",
             "message": f"the sidecar carries {carries or 'no card id'}, its note is {card_id_from(card)}"}]
