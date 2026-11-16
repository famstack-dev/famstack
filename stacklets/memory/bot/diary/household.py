"""The names this household uses, for whisper and for a card.

    household("Homer,Marge", pages)            # ["Homer", "Marge", "Maggie"]
    spoken_vocabulary(["Homer", "Maggie"])     # "The people in this family are Homer and Maggie."
    card_people(persons, ["Homer", "Marge"])   # {"margaret": "Maggie", "homer": "Homer", ...}

Whisper decodes a recording against the household's vocabulary, so the
names it would otherwise hear as ordinary words come back right; a card
may name only the people listed here. Pure: the command reads
users.toml and the person pages and passes them in.
"""

from __future__ import annotations

from .texts import and_list, phrase

# ── What this household says ──────────────────────────────────────────


def spoken_vocabulary(people, topics=()) -> str:
    """A hint for whisper about the words this family uses.

    Whisper decodes against it, so names it would otherwise hear as
    ordinary words come back right. This is the only place a name can be
    fixed: the polish pass is forbidden from changing words, and it is
    right to be -- the memories room holds what people said to their
    children, and a model quietly editing that is not a transcript any
    more. So the input is corrected instead of the output.

    Phrased as a sentence rather than a bare list because that is what
    the parameter is for: whisper treats it as preceding speech, and a
    list of nouns biases the decoder toward answering in lists.
    """
    names = [n.strip() for n in people if n and n.strip()]
    subjects = [t.strip() for t in topics if t and t.strip()]
    parts = []
    if names:
        parts.append(phrase("vocab_people").format(
            names=and_list(list(dict.fromkeys(names)))))
    if subjects:
        parts.append(phrase("vocab_topics").format(
            topics=and_list(list(dict.fromkeys(subjects)))))
    return " ".join(parts)


def household(family_names: str, pages) -> list[str]:
    """The names this family's people go by: users.toml, then the wiki.

    `family_names` is the comma-separated FAMILY_NAMES from users.toml,
    so everyone counts from the first day, before any filing names them.
    The person pages (their frontmatter) add the family's own spellings,
    nicknames and the people without an account. A name already listed is
    not repeated, whatever its case.
    """
    names = [n.strip() for n in family_names.split(",") if n.strip()]
    for page in pages:
        if page.get("type") != "person":
            continue
        name = page.get("canonical") or page.get("title")
        names.extend(str(n) for n in [name, *(page.get("synonyms") or [])] if n)
    seen: set[str] = set()
    return [n for n in names if not (n.lower() in seen or seen.add(n.lower()))]


def card_people(persons, household) -> dict[str, str]:
    """Every name a card may use, lower-cased, to the person it means.

    The person pages map each known name to its canonical form; everyone
    else in the household counts under their own name, so a member no
    filing has named yet can still be who a note is about.
    """
    people = {name.lower(): p.canonical for p in persons for name in p.all_known_names()}
    for name in household:
        people.setdefault(name.lower(), name)
    return people
