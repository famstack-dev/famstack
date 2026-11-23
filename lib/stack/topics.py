"""The topics and the ontology, read from the memory.

The classifier, the diary and the search name the same topics.
The memory vault stores them in two places, on top of the shipped seed:

    <vault>/ontology.toml                the ontology (else the shipped seed)
    <vault>/<bucket>/<topic>/topic.md    a topic card: a topic of its own

    get_ontology(vault, seed_path)       # the ontology with the topic cards on top
    topic_cards(vault)                   # [TopicCard("garden", "Garden", "shared/garden/topic.md")]
    topic_card_titles(vault)             # {"bike-trip": "Bike Trip"}

The module lives in the framework because several stacklets read the same topics.
A stacklet imports no other stacklet.
The module reads files and uses only the standard library.
The memory stacklet re-exports these names.
It passes its shipped seed to `get_ontology`, because the framework does not know where a stacklet keeps its files.
`stack.people` reads the people.

`get_ontology` is the admin's view. It returns the topic cards of every bucket.
A writer that tags one card reads the topics of the card's place instead.
These are the topic cards that the card's readers may read (`stack.filing.vocabulary`).

Debt: some readers that search or label still read the admin's view.
A private topic of one person can therefore reach another reader through them.
The readers are:

- the search (`memory.lib.search_memory`, the archivist's `search_service`),
- the graph, `stack memory lookup` and `prompt`, and the query rewrite.

Notes, the diary, documents, email and the tag passes of the migrations read
the vocabulary of the place (`stack.filing.vocabulary`).
Trigger: the search answers per reader, or the prompt of a document shows topic titles to the model.
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

    The function returns None when the vault has no ontology file.
    A fresh clone has none before the seed push lands. A hand-curated vault can lack it too.
    """
    ontology_file = Path(vault_path) / ONTOLOGY_PATH_IN_REPO
    if not ontology_file.exists():
        return None
    return Ontology.load(ontology_file)


def get_ontology(vault_path: Optional[Path], seed_path: Path, *,
                 shared_bucket: str = DEFAULT_SHARED_BUCKET) -> Ontology:
    """Return the live ontology of the vault, else the shipped seed, with the topic cards on top.

    The caller passes the vault path, so the function reads no environment.
    The CLI commands read the path from `config["data_dir"]`.
    The install hook reads it from `ctx.stack.data`.
    The archivist reads it from `MEMORY_VAULT_DIR` in its container.

    A topic card declares a topic of its own (`topic_card_titles`).
    Each writer and reader of tags takes its topics from here.
    The topic cards are then topics for the tag rule, the search, the format check and the card schemas
    (`stack.tags.with_declared_topics`).
    A classifier is still offered the topics of the ontology alone.

    `seed_path` is the shipped seed, which the memory stacklet owns.
    With `vault_path` set to `None`, the function reads the seed alone.
    Use that before the install and in tests.
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

    >>> topic_declared_at("shared/bike-trip/topic.md")
    'bike-trip'
    >>> topic_declared_at("shared/bike-trip/notes/2026/10/tent-3a338e.md")
    ''
    """
    parts = path.split("/")
    return parts[1] if len(parts) == 3 and parts[2] in TOPIC_CARDS else ""


class TopicCard(NamedTuple):
    """One topic card: the topic id it declares, its title, its vault path and its former ids.

    The path says who may read the topic (`stack.cards.audience.readable`).
    `TopicCard("surprise", "Surprise", "alex/surprise/topic.md")` is private to alex.
    `former` holds the `former_topic_ids` of a renamed topic, oldest first:
    `TopicCard("quilt", "Quilt", "shared/quilt/topic.md", ("blanket",))`.
    """

    id: str
    title: str
    path: str
    former: tuple[str, ...] = ()


def topic_cards(vault_path: Path, *, shared_bucket: str = DEFAULT_SHARED_BUCKET) -> list[TopicCard]:
    """Every topic card in the vault: the cards of the shared bucket first, then the others in path order.

    A topic card is `<bucket>/<folder>/topic.md` with `type: topic`.
    Its folder is the topic id.
    The function leaves out a file that does not read as a topic card.
    Two buckets can each have a card for one id.
    The function lists both, because the people of each bucket read their own card.
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
    """Map each topic that the topic cards declare, by its id (the folder of the card), to its title.

    A topic card (`<bucket>/<folder>/topic.md`, `type: topic`) is a topic of its own on top of the ontology.
    Buckets can have a folder of one name.
    The card of the shared bucket then titles the id, else the first card in path order.
    A search label or a shared card then never shows the title of one person's topic
    (`sam/garden` beside `shared/garden`).
    """
    titles: dict[str, str] = {}
    for card in topic_cards(vault_path, shared_bucket=shared_bucket):
        titles.setdefault(card.id, card.title)
    return titles


def _former_ids(value) -> tuple[str, ...]:
    """A topic card's `former_topic_ids` as a tuple of ids; one id alone is a list of one."""
    values = value if isinstance(value, list) else [value] if value else []
    return tuple(str(v).strip() for v in values if str(v).strip())
