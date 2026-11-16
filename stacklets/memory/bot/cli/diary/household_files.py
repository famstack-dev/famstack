"""What the diary command knows of the household, read from its files.

    household_vocabulary()    # "The people in this family are Homer and Maggie. ..."
    household_people()        # ["Homer", "Marge", "Maggie"]
    bucket_vocabulary("family")      # the ontology, the names, the prompt's family block, ...

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
from stack.frontmatter import FrontmatterError
from stack.frontmatter import parse as parse_frontmatter
from stack.people import Person as DeclaredPerson
from stack.people import find_people
from stack.tags import tag_label

from ....lib import Person, get_ontology, load_persons_from_vault, persons_prompt_section
from ...diary.household import card_people, household, spoken_vocabulary
from .progress import err

# Whisper's decoder prompt is a small window (a couple of hundred
# tokens); past it the hint is truncated from the front, which would
# drop the names silently. Names first, topics only with room to spare.
_VOCAB_BUDGET = 600


def _declared() -> list[DeclaredPerson] | None:
    """The family's person files; None until `stack up memory` wrote them.

    A file that does not read is logged with its path and left out.
    """
    return find_people(
        (os.environ.get("MEMORY_VAULT_DIR"), os.environ.get("BRAIN_REPO_DIR")),
        os.environ.get("SHARED_BUCKET") or "family",
        report=lambda problem: err(f"person file skipped: {problem}"),
    )


def household_people() -> list[str]:
    """The household's names: the person files, then the wiki's person pages.

    The person files declare everyone the family knows, a baby without
    an account too, with every name the family uses for them. Until they
    exist the accounts stand in (FAMILY_NAMES, from users.toml). The
    pages add the spellings documents used. Best-effort: no brain means
    the declared names alone.
    """
    brain = Path(os.environ.get("BRAIN_REPO_DIR", ""))
    abouts = sorted(brain.glob("*/about.md")) if brain.is_dir() else []
    declared = _declared()
    if declared is None:
        return household(os.environ.get("FAMILY_NAMES", ""), [_frontmatter(a) for a in abouts])
    # A person's page is the folder named by their id (`<id>/about.md`).
    ids = {p.id for p in declared}
    names = ",".join(n for p in declared for n in p.all_names())
    return household(names, [_frontmatter(a) for a in abouts if a.parent.name in ids])


def _declared_persons(declared: list[DeclaredPerson], learned: list[Person]) -> list[Person]:
    """The person files as the classifier's persons: first name canonical.

    The first name is the name cards carry and the `Person:` tag the
    archivist files under, so a card about "Margaret" lists Maggie the
    same way a document about her is tagged. The full name, the
    family's aliases and the spellings documents used are the synonyms.
    """
    by_slug = {p.slug: p for p in learned}
    persons = []
    for d in declared:
        names = [n for n in d.all_names() if n != d.first_name]
        seen = by_slug.get(d.id)
        for n in seen.all_known_names() if seen else []:
            if n.lower() not in {x.lower() for x in [d.first_name, *names]}:
                names.append(n)
        persons.append(Person(canonical=d.first_name, slug=d.id, synonyms=names))
    return persons


def household_vocabulary() -> str:
    """The names and subjects this family uses, for whisper to decode against.

    People come from users.toml and the wiki's person pages. The ontology's topics
    follow, because a family's proper nouns are not only its people --
    a campsite, a school, a pet -- and those mishear just as readily.

    Best-effort: a vault that has not been generated yet simply yields
    nothing, and transcription proceeds exactly as it did before.
    """
    people = household_people()

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


def _frontmatter(path: Path) -> dict:
    """A page's frontmatter, or {} when it has none that parses (and says so)."""
    try:
        return parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    except FrontmatterError as e:
        err(f"{path}: frontmatter does not parse ({e}), skipped")
        return {}


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
    return get_ontology(vault_dir if vault_dir.is_dir() else None)


def topic_labels(tags, ontology, language: str) -> list[str]:
    """A card's topics as a person reads them, and as the model reads them
    beside the vocabulary it is offered: a topic's label in the household
    language (`stack.tags.tag_label`), a keyword as it is written. A card
    tags a topic by its id (ADR-017 §4); a `Person:` tag, from before
    people had ids, names no topic."""
    return [tag_label(t, ontology, language) for t in tags if not t.startswith("Person: ")]


def bucket_vocabulary(bucket: str):
    """What the model is offered and held to: the ontology, the family's
    names (canonical form by every known name), the family block for the
    prompt, the household language code, and the declared people the
    names resolve to ids through (None until their files exist)."""
    language_code = household_language_code()
    ontology = household_ontology()
    # Person pages are generated, so they live in the brain, not the vault.
    brain_dir = Path(os.environ.get("BRAIN_REPO_DIR", ""))
    persons = load_persons_from_vault(brain_dir, bucket) if brain_dir.is_dir() else []
    declared = _declared()
    if declared is not None:
        persons = _declared_persons(declared, persons)
    people = card_people(persons, household_people())
    # The pages describe the people they know; the rest of the household
    # joins the list by name, so the model can still say a note is about them.
    others = sorted(set(people.values()) - {p.canonical for p in persons})
    if persons:
        section = persons_prompt_section(persons) + "".join(f"\n  - {n}" for n in others)
    else:
        section = "Family members: " + ", ".join(others)
    return ontology, people, section, language_code, declared
