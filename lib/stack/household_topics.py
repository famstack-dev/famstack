"""The household's topics and ontology, read from the memory.

The classifier, the diary and the search name the same topics. They are
stored in the memory vault in two places, on top of the shipped seed:

    <vault>/ontology.toml                the ontology (else the shipped seed)
    <vault>/<bucket>/<topic>/topic.md    a topic card: the household's own topic

    get_ontology(vault, seed_path)       # the ontology with the topic cards on top
    topic_cards(vault)                   # [TopicCard("van", "Camper Van", "family/van/topic.md")]
    topic_card_titles(vault)             # {"itchy-scratchy-land": "Itchy & Scratchy Land"}

Lives in the framework because the readers span stacklets: the archivist
and the diary read one vocabulary, and a stacklet imports no other
stacklet. Pure file reads, stdlib only. The memory stacklet re-exports
these names and passes its shipped seed to `get_ontology`, because the
framework does not know where a stacklet keeps its files. The household's
people are read by `stack.household_people`.

`get_ontology` is the admin's view: every bucket's topic cards. A writer
that tags one card reads the topics of that card's place instead, which
are only the topic cards its readers may read (`stack.filing.vocabulary`).

Debt: readers that tag, search or label for one
person still read the admin's view here, so a person's private topic can
still reach another reader through them: search (`memory.lib.search_memory`),
the tag passes of the migrations (`topic_tags_for`), the graph, `stack
memory lookup` and `prompt`, the query rewrite, and the archivist's
documents and email (`git_mirror._tags`, `document_pipeline`,
`search_service`). Notes and the diary read the place's vocabulary
(`stack.filing.vocabulary`). Trigger: search answers per reader, or a
document's prompt shows topic titles to the model.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple, Optional

from .cards import FORMER_TOPIC_IDS
from .frontmatter import card_fields
from .ontology import Ontology
from .tags import with_declared_topics
from .vault import DEFAULT_SHARED_BUCKET, TOPIC_CARD

# The ontology's file in the vault's root.
ONTOLOGY_PATH_IN_REPO = "ontology.toml"


def load_ontology_from_vault(vault_path: Path) -> Optional[Ontology]:
    """Read `ontology.toml` from the vault working copy.

    Returns None when the vault has no ontology file yet (a freshly-
    cloned repo where the seed push hasn't landed, or a hand-curated
    instance that deleted the file).
    """
    ontology_file = Path(vault_path) / ONTOLOGY_PATH_IN_REPO
    if not ontology_file.exists():
        return None
    return Ontology.load(ontology_file)


def get_ontology(vault_path: Optional[Path], seed_path: Path, *,
                 shared_bucket: str = DEFAULT_SHARED_BUCKET) -> Ontology:
    """Return the live ontology from the vault, else the shipped seed, with
    the topics the vault's topic cards declare on top of it.

    Callers pass an explicit vault path so this stays a pure function
    with no env or framework reach. The CLI commands read the path
    from `config["data_dir"]`, the install hook from `ctx.stack.data`,
    and the archivist from `MEMORY_VAULT_DIR` in its container.

    A topic card is the household's own topic (`topic_card_titles`). Every
    writer and reader of tags takes its topics from here, so the topic
    cards are topics for the tag rule, the search, the format check and
    the card schemas alike (`stack.tags.with_declared_topics`), while a
    classifier is still offered the ontology's alone.

    `seed_path` is the shipped seed, which the memory stacklet owns.
    Calling with `None` is the pre-install / test-friendly path:
    seed-only, no filesystem touch beyond the shipped file.
    """
    if vault_path is None:
        return Ontology.load(seed_path)
    ontology = load_ontology_from_vault(vault_path) or Ontology.load(seed_path)
    return with_declared_topics(ontology, topic_card_titles(Path(vault_path), shared_bucket=shared_bucket))


# A topic's card, in the topic's folder (ADR-017 §10). A memory from before
# topic cards had a file of their own keeps it as `about.md` there.
TOPIC_CARDS = (TOPIC_CARD, "about.md")


def topic_declared_at(path: str) -> str:
    """The topic id a topic card at vault path `path` declares, its folder; "" elsewhere.

    >>> topic_declared_at("family/itchy-scratchy-land/topic.md")
    'itchy-scratchy-land'
    >>> topic_declared_at("family/itchy-scratchy-land/notes/2026/10/tent-3a338e.md")
    ''
    """
    parts = path.split("/")
    return parts[1] if len(parts) == 3 and parts[2] in TOPIC_CARDS else ""


class TopicCard(NamedTuple):
    """One topic card: the topic id it declares, its title, its vault path
    and the ids it had before a rename.

    The path says who may read the topic (`stack.cards.audience.readable`):
    `TopicCard("surprise-party", "Homers 40th", "marge/surprise-party/topic.md")`
    is Marge's alone. `former` holds its `former_topic_ids`, oldest first:
    `TopicCard("lisas-quilt", "Lisa's Quilt", "family/lisas-quilt/topic.md",
    ("quilt-for-lisa",))`.
    """

    id: str
    title: str
    path: str
    former: tuple[str, ...] = ()


def topic_cards(vault_path: Path, *, shared_bucket: str = DEFAULT_SHARED_BUCKET) -> list[TopicCard]:
    """Every topic card in the vault, the shared bucket's first, then the
    others in path order.

    A topic card is `<bucket>/<folder>/topic.md` with `type: topic`; its
    folder is the topic id. A file that does not read as one is left out.
    Two buckets may each have a card for one id: both are listed, because
    each is read by its own bucket's people.
    """
    cards: list[TopicCard] = []
    for name in TOPIC_CARDS:
        for file in sorted(Path(vault_path).glob(f"*/*/{name}")):
            try:
                fields = card_fields(file.read_text(encoding="utf-8"), file)
            except (OSError, UnicodeDecodeError):
                continue
            if fields is None or str(fields.get("type") or "").strip() != "topic":
                continue
            path = file.relative_to(vault_path).as_posix()
            cards.append(TopicCard(topic_declared_at(path), str(fields.get("title") or "").strip(), path,
                                   _former_ids(fields.get(FORMER_TOPIC_IDS))))
    # Stable: within each group the order above (topic.md first, then path) stays.
    return sorted(cards, key=lambda card: card.path.partition("/")[0] != shared_bucket)


def topic_card_titles(vault_path: Path, *, shared_bucket: str = DEFAULT_SHARED_BUCKET) -> dict[str, str]:
    """Each topic the vault's topic cards declare: its id, the card's folder, to its title.

    A topic card (`<bucket>/<folder>/topic.md`, `type: topic`) is the
    household's own topic on top of the ontology. When buckets have a
    folder of one name, the shared bucket's card titles the id, else the
    first in path order: a search label or a family card never shows the
    title of one person's topic (`bart/garden` beside `family/garden`).
    """
    titles: dict[str, str] = {}
    for card in topic_cards(vault_path, shared_bucket=shared_bucket):
        titles.setdefault(card.id, card.title)
    return titles


def _former_ids(value) -> tuple[str, ...]:
    """A topic card's `former_topic_ids` as a tuple of ids; one id alone is a list of one."""
    values = value if isinstance(value, list) else [value] if value else []
    return tuple(str(v).strip() for v in values if str(v).strip())
