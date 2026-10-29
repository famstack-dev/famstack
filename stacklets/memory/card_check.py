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
  its `former_card_ids`, as a card of a new type does (ADR-016). A copy,
  a file whose id another file still carries after the write, may get an
  id of its own: of two files with one id, migration 0002 and the curator
  give the newer one a new id (ADR-016 §2).
- **A sidecar is its card's.** The sidecar beside a note (ADR-018) carries
  the note's `card_id`.
- **A topic moves whole, and nothing is filed under its old id.** Both are
  judged on the memory as the write leaves it, its removals included:
  - A write that adds an id to a topic card's `former_topic_ids` (`stack
    memory topic rename`) is refused while a file it does not remove is
    still under that id's folder: a capture committed between the
    rename's read and its commit would stay behind and split the topic.
    The rename is read and made again.
  - A write that would make the empty folder of an id a topic card of the
    bucket lists among its `former_topic_ids` is refused, naming the
    folder the topic is in now: a capture classified while the rename was
    committed would otherwise split the topic too. A rename back moves the
    card that lists the id, and takes the id off it, so it is not refused.
- **The ontology parses.** `ontology.toml` holds the topics a card may
  name; one that does not parse is not written, and while the memory's
  own does not (a hand edit in Forgejo), a card is refused naming it,
  since its topics cannot be checked.

A migration's commit (one with a `Migration:` trailer) is not held to the
schemas: migrations bring old cards into the format in steps.

Each entry is `{"path", "field", "message"}`, with `"allowed"` where the
schema lists what may go there. Runs in the curator's container, which
carries `jsonschema`.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .card_schema import CardSchemas, VaultValues
from stack.cards import card_id_from, former_card_ids
from stack.cards import FORMER_TOPIC_IDS
from stack.cards.lookup import paths_with_card_id
from stack.cards.tree import holds_a_file
from stack.cards.sources import sidecar_path
from stack.frontmatter import FrontmatterError, parse
from stack.people import load_people
from stack.vault import DEFAULT_SHARED_BUCKET, TOPIC_CARD

# What a check reads the memory as it stands with: a path's text, or None.
Reader = Callable[[str], "str | None"]

# Where the memory keeps its topics (`memory.lib.ONTOLOGY_PATH_IN_REPO`).
ONTOLOGY_FILE = "ontology.toml"


def _memory_ontology() -> Callable:
    """The memory's ontology reader (`memory.lib.get_ontology`), taken when
    the check is built, where the curator builds it, not when a request
    comes in."""
    from .lib import get_ontology
    return get_ontology


@dataclass
class CardCheck:
    """The checks the card store runs on a write's files; see the module."""

    shared_bucket: str = DEFAULT_SHARED_BUCKET
    schemas: CardSchemas = field(default_factory=CardSchemas.load)
    ontology_of: Callable = field(default_factory=_memory_ontology)

    def __call__(self, puts: Sequence[tuple[str, str]], read: Reader, repo, *,
                 formats: bool = True, removes: Sequence[str] = ()) -> list[dict]:
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
            problems += _identity(path, fields, read(path), cards=cards, repo=repo)
        problems += _topic_moves(puts, removes, Path(repo))
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


def _identity(path: str, fields: dict, before: str | None, *,
              cards: dict[str, dict], repo) -> list[dict]:
    had = card_id_from(_fields(before) or {}) if before is not None else ""
    now = card_id_from(fields)
    if not had or now == had or had in former_card_ids(fields):
        return []
    if _carried_elsewhere(had, path, cards, repo):
        return []
    return [{"path": path, "field": "card_id",
             "message": f"the card is {had}; a change never makes it {now or 'a card without an id'}"}]


def _carried_elsewhere(card_id: str, path: str, cards: dict[str, dict], repo) -> bool:
    """Whether a file besides `path` carries `card_id` once the write is
    done. Then `path` is a copy of that card, and the id stays with the
    card it names when the copy gets one of its own (ADR-016 §2). Scans
    the clone, so it runs only for a write that changes an id."""
    if any(card_id_from(fields) == card_id for other, fields in cards.items() if other != path):
        return True
    return any(other not in cards for other in paths_with_card_id(card_id, root=repo))


def _topic_moves(puts: Sequence[tuple[str, str]], removes: Sequence[str],
                 repo: Path) -> list[dict]:
    """What is wrong with how the write moves topics, on the memory as it leaves it."""
    removed = set(removes)
    put_cards = {path: _former_topic_ids(text) for path, text in puts if _is_topic_card(path)}
    after = {rel: former for card in repo.glob(f"*/*/{TOPIC_CARD}")
             if (rel := card.relative_to(repo).as_posix()) not in removed | put_cards.keys()
             and (former := _former_topic_ids(card.read_text(encoding="utf-8", errors="replace")))}
    after.update(put_cards)
    problems = []
    for path, former in put_cards.items():
        bucket = path.split("/", 1)[0]
        for topic in sorted(set(former) - _former_before(path, puts, removed, repo)):
            if left := _files_under(repo / bucket / topic, repo, removed):
                problems.append({"path": path, "field": FORMER_TOPIC_IDS, "message": (
                    f"{left[0]} is still in {bucket}/{topic}/ and this write does not move it, "
                    f"so the topic would be split: it was filed after the rename read the "
                    f"memory. Run the rename again")})
    for path, _ in puts:
        parts = PurePosixPath(path).parts
        if len(parts) < 3 or holds_a_file(repo / parts[0] / parts[1]):
            continue
        now = next((card.split("/")[1] for card, former in after.items()
                    if card.split("/")[0] == parts[0] and card.split("/")[1] != parts[1]
                    and parts[1] in former), "")
        if now:
            problems.append({"path": path, "field": "path", "message": (
                f"the topic {parts[1]} was renamed to {now}: file it under "
                f"{parts[0]}/{now}/")})
    return problems


def _is_topic_card(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return len(parts) == 3 and parts[2] == TOPIC_CARD


def _former_topic_ids(text: str) -> list[str]:
    fields = _fields(text) or {}
    former = fields.get(FORMER_TOPIC_IDS) or []
    return [str(f) for f in (former if isinstance(former, list) else [former])]


def _former_before(path: str, puts: Sequence[tuple[str, str]], removed: set[str],
                   repo: Path) -> set[str]:
    """The former ids the topic card the write puts at `path` had before it:
    at the same path, or at the topic card the write removes with its id."""
    if (repo / path).is_file():
        return set(_former_topic_ids((repo / path).read_text(encoding="utf-8")))
    card_id = card_id_from(_fields(dict(puts)[path]) or {})
    for gone in removed:
        if _is_topic_card(gone) and (repo / gone).is_file():
            text = (repo / gone).read_text(encoding="utf-8")
            if card_id and card_id_from(_fields(text) or {}) == card_id:
                return set(_former_topic_ids(text))
    return set()


def _files_under(folder: Path, repo: Path, removed: set[str]) -> list[str]:
    """The files under `folder` the write leaves there, by path."""
    if not folder.is_dir():
        return []
    return sorted(rel for f in folder.rglob("*") if f.is_file()
                  and (rel := f.relative_to(repo).as_posix()) not in removed)


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
