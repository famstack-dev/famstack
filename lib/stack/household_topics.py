"""The household's topics and ontology, read from the memory.

The classifier, the diary and the search name the same topics. They are
stored in the memory vault in two places, on top of the shipped seed:

    <vault>/ontology.toml                the ontology (else the shipped seed)
    <vault>/<bucket>/<topic>/topic.md    a topic card: the household's own topic

    get_ontology(vault, seed_path)       # the ontology with the topic cards on top
    topic_card_titles(vault)             # {"itchy-scratchy-land": "Itchy & Scratchy Land"}

Lives in the framework because the readers span stacklets: the archivist
and the diary read one vocabulary, and a stacklet imports no other
stacklet. Pure file reads, stdlib only. The memory stacklet re-exports
these names and passes its shipped seed to `get_ontology`, because the
framework does not know where a stacklet keeps its files. The household's
people are read by `stack.household_people`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .frontmatter import card_fields
from .ontology import Ontology
from .tags import with_declared_topics
from .vault import TOPIC_CARD

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


def get_ontology(vault_path: Optional[Path], seed_path: Path) -> Ontology:
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
    return with_declared_topics(ontology, topic_card_titles(Path(vault_path)))


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


def topic_card_titles(vault_path: Path) -> dict[str, str]:
    """Each topic the vault's topic cards declare: its id, the card's folder, to its title.

    A topic card (`<bucket>/<folder>/topic.md`, `type: topic`) is the
    household's own topic on top of the ontology. Buckets with a folder of
    one name declare one topic id, titled by the first card in path order.
    A file that does not read as a topic card declares nothing.
    """
    titles: dict[str, str] = {}
    for name in TOPIC_CARDS:
        for file in sorted(Path(vault_path).glob(f"*/*/{name}")):
            try:
                fields = card_fields(file.read_text(encoding="utf-8"), file)
            except (OSError, UnicodeDecodeError):
                continue
            if fields is None or str(fields.get("type") or "").strip() != "topic":
                continue
            topic = topic_declared_at(file.relative_to(vault_path).as_posix())
            titles.setdefault(topic, str(fields.get("title") or "").strip())
    return titles
