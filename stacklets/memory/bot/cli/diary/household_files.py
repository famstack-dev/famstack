"""What the diary command knows of the household, read from its files.

    household_vocabulary()    # "The people in this family are Homer and Maggie. ..."
    known_names()             # ["Homer", "Marge", "Maggie"]
    bucket_vocabulary("family")      # the bucket's topics, the names, the prompt's family block, ...

The person files (`stack.people`), the wiki's person pages, the ontology
and the household language. Whisper decodes against the names, and the
model that reads an entry may name only the people listed here. The
pure half, which turns names into a hint and a lookup, is
`bot/diary/household.py`.
"""

from __future__ import annotations

import os
from pathlib import Path

from stack.ai.language import LANGUAGES, language_code
from stack.tags import tag_label
from stack.vault import DEFAULT_SHARED_BUCKET

from stack.people.names import (card_people, declared_as_persons, declared_people,
                              known_names, load_persons_from_vault, persons_prompt_section)

from stack.filing.vocabulary import Vocabulary

from ....lib import SEED_ONTOLOGY_PATH, get_ontology
from ...diary.household import spoken_vocabulary
from .progress import err

# Whisper's decoder prompt is a small window (a couple of hundred
# tokens); past it the hint is truncated from the front, which would
# drop the names silently. Names first, topics only with room to spare.
_VOCAB_BUDGET = 600


def household_vocabulary() -> str:
    """The names and subjects this family uses, for whisper to decode against.

    People come from users.toml and the wiki's person pages. The ontology's topics
    follow, because a family's proper nouns are not only its people --
    a campsite, a school, a pet -- and those mishear just as readily.

    Best-effort: a vault that has not been generated yet simply yields
    nothing, and transcription proceeds exactly as it did before.
    """
    people = known_names()

    topics: list[str] = []
    vault = Path(os.environ.get("MEMORY_VAULT_DIR", ""))
    lang = os.environ.get("LANGUAGE", "en")
    try:
        from stack.ontology import Ontology
        loaded = Ontology.load(vault / "ontology.toml")
        for topic in loaded.topics.values():
            topics.append(topic.name(lang))
            topics.extend(topic.synonyms_for(lang))
    except Exception as e:
        err(f"  no ontology for the transcript hint: {e}")

    hint = spoken_vocabulary(people, topics)
    if len(hint) <= _VOCAB_BUDGET:
        return hint
    # Over budget: keep the people, who are what mishears most.
    return spoken_vocabulary(people)[:_VOCAB_BUDGET]


# The prompt is English, so without a stated target language the model
# answers in English. The household language comes from the core env.
def household_language() -> str:
    return LANGUAGES.get(language_code(os.environ.get("LANGUAGE") or ""),
                         "the language the entries are written in")


def household_language_code() -> str:
    """The household language's code, `en` when none is set."""
    return (os.environ.get("LANGUAGE") or "en").strip().lower()[:2]


def household_ontology():
    """The topics the household tags with: the memory's own ontology, else the seed."""
    vault_dir = Path(os.environ.get("MEMORY_VAULT_DIR", ""))
    return get_ontology(vault_dir if vault_dir.is_dir() else None,
                        shared_bucket=os.environ.get("SHARED_BUCKET") or DEFAULT_SHARED_BUCKET)


def topic_labels(tags, ontology, language: str) -> list[str]:
    """A card's topics as a person reads them, and as the model reads them
    beside the vocabulary it is offered: a topic's label in the household
    language (`stack.tags.tag_label`), a keyword as it is written. A card
    tags a topic by its id (ADR-017 §4); a `Person:` tag, from before
    people had ids, names no topic."""
    return [tag_label(t, ontology, language) for t in tags if not t.startswith("Person: ")]


def bucket_vocabulary(bucket: str):
    """What the model is offered and held to: the topics of `bucket`
    (`stack.filing.vocabulary.Vocabulary`: the ontology's and the topic
    cards everyone who reads `bucket` may read), the family's names
    (canonical form by every known name), the family block for the
    prompt, the household language code, and the declared people the
    names resolve to ids through (None until their files exist)."""
    language_code = household_language_code()
    # SAFETY: the topics of the card's bucket, not the admin's view of
    # every bucket: a person's own topic is offered only for their cards.
    vocabulary = bucket_topics(bucket)
    # Person pages are generated, so they live in the brain, not the vault.
    brain_dir = Path(os.environ.get("BRAIN_REPO_DIR", ""))
    persons = load_persons_from_vault(brain_dir, bucket) if brain_dir.is_dir() else []
    declared = declared_people()
    if declared is not None:
        persons = declared_as_persons(declared, persons)
    people = card_people(persons, known_names())
    # The pages describe the people they know; the rest of the household
    # joins the list by name, so the model can still say a note is about them.
    others = sorted(set(people.values()) - {p.canonical for p in persons})
    if persons:
        section = persons_prompt_section(persons) + "".join(f"\n  - {n}" for n in others)
    else:
        section = "Family members: " + ", ".join(others)
    return vocabulary, people, section, language_code, declared


def bucket_topics(bucket: str) -> Vocabulary:
    """The topics a diary card in `bucket` may carry: the memory's ontology
    (else the seed) and the topic cards everyone who reads `bucket` may read."""
    vault_dir = Path(os.environ.get("MEMORY_VAULT_DIR", ""))
    return Vocabulary.of(bucket, vault_dir if vault_dir.is_dir() else None, seed=SEED_ONTOLOGY_PATH,
                         shared_bucket=os.environ.get("SHARED_BUCKET") or DEFAULT_SHARED_BUCKET)
