"""The names of the people, read from the person files and the wiki's person pages.

The classifier, the diary and the transcription name the same people.
The person files declare them (`stack.people.files`).
The wiki's person pages (`<vault>/<member>/about.md`) add the spellings that documents used.

    load_persons_from_vault(vault)         # [PersonPage("Alex", "alex", ["Al"])]
    known_names()                          # ["Alex", "Sam", "Jo"]
    card_people(persons, known)            # {"al": "Alex", "alex": "Alex", ...}

The module lives in the framework because several stacklets read the same people.
A stacklet imports no other stacklet.
The module reads files and environment variables and uses only the standard library.
The memory stacklet re-exports these names.
`stack.topics` reads the topics.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ..frontmatter import FrontmatterError, card_fields, parse as _parse_frontmatter_new
from .files import Person as DeclaredPerson
from .files import find_people
from ..vault import DEFAULT_SHARED_BUCKET

# ─── People ──────────────────────────────────────────────────────────────

# Skip set for `load_persons_from_vault`: top-level directories that
# carry an `about.md` but are not a member. Keep in sync
# with the `stack memory wiki` command's own skip set; both walk the
# same vault structure.
_NON_MEMBER_DIRS = {".git", ".obsidian", "wiki", "private", "templates", "_shared"}


@dataclass
class PersonPage:
    """A person's wiki page (`<vault>/<id>/about.md`): the names it lists for one member.

    A declared person is a `stack.people.Person`.
    """

    canonical: str
    slug: str
    synonyms: List[str] = field(default_factory=list)
    # Path on disk, useful for `stack memory persons show` and tests.
    source_path: Optional[Path] = None

    def all_known_names(self) -> List[str]:
        """Canonical + every synonym, no duplicates, canonical first."""
        seen = {self.canonical}
        out = [self.canonical]
        for s in self.synonyms:
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out


def load_persons_from_vault(
    vault_path: Path,
    shared_bucket: str = DEFAULT_SHARED_BUCKET,
) -> List[PersonPage]:
    """Walk `<vault>/<slug>/about.md` and return the parsed entries.

    Each bucket directly under the vault root can be a member.
    The reader skips the reserved buckets (the `shared_bucket`, `wiki/`, `.git/`) by name.
    The reader skips a bucket without an `about.md`,
    because a member can have notes before the wiki writes their page.
    The reader skips a page whose `kind:` is not `person`,
    because other kinds of page can share the directory shape.
    """
    vault_path = Path(vault_path)
    if not vault_path.exists():
        return []

    skip = _NON_MEMBER_DIRS | {shared_bucket}
    result: List[PersonPage] = []
    for about in sorted(vault_path.glob("*/about.md")):
        slug = about.parent.name
        if slug in skip or slug.startswith("."):
            continue
        try:
            text = about.read_text(encoding="utf-8")
        except OSError:
            continue
        # A broken card is never guessed at: its folder is no person's name.
        if (meta := card_fields(text, about)) is None:
            continue
        if meta.get("kind") and meta.get("kind") != "person":
            continue
        canonical = meta.get("canonical") or meta.get("title") or slug
        if not canonical:
            continue
        result.append(PersonPage(
            canonical=str(canonical),
            slug=str(meta.get("slug") or slug),
            synonyms=fm_list(meta, "synonyms"),
            source_path=about,
        ))
    return result


# ─── Frontmatter helpers ─────────────────────────────────────────────────

def parse_frontmatter(text: str, path: Path | str | None = None) -> dict:
    """Parse vault entry frontmatter using the shared stdlib-only module.

    Delegates to stack.frontmatter.parse, which handles the strict
    §2 subset per the vault format spec. A page whose frontmatter does
    not parse is read without it, so search still finds it by its text,
    and none of its fields are guessed. Pass the page's `path` to have
    that reported on stderr; a reader that must leave such a card out
    rather than read it as one without fields calls `card_fields`.
    """
    if path is not None:
        return card_fields(text, path) or {}
    try:
        return _parse_frontmatter_new(text)
    except FrontmatterError:
        return {}


def fm_list(fm: dict, key: str) -> List[str]:
    """Read a frontmatter field as a list of strings, regardless of shape."""
    v = fm.get(key)
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        return [v]
    return []


# ─── Known names ─────────────────────────────────────────────────────────

def _say(message: str) -> None:
    """Report on stderr: a command's log, which a reader of files must not fail on."""
    print(message, file=sys.stderr)


def persons_prompt_section(persons: List[PersonPage]) -> str:
    """Render the persons block for the classifier prompt.

    The block has this shape, as in `correspondents_prompt_section`:

        Family members (canonical first name; synonyms in parens):
          - Alex (Alexander, Alex Doe)
          - Sam

    The function returns an empty string when no person page exists.
    The prompt builder then uses the flat list of first names from the Paperless Person tags.
    """
    if not persons:
        return ""
    lines = ["Family members (canonical first name; synonyms in parens):"]
    for p in persons:
        if p.synonyms:
            lines.append(f"  - {p.canonical} ({', '.join(p.synonyms)})")
        else:
            lines.append(f"  - {p.canonical}")
    return "\n".join(lines)


def merge_names(account_names: str, pages) -> list[str]:
    """The names the people go by: the account names first, then the person pages.

    `account_names` is the comma-separated FAMILY_NAMES from users.toml.
    Everyone counts from the first day because of it, before any filing names them.
    The frontmatter of the person pages adds spellings, nicknames and people without an account.
    A name that is already listed is not repeated, whatever its case.
    """
    names = [n.strip() for n in account_names.split(",") if n.strip()]
    for page in pages:
        if page.get("type") != "person":
            continue
        name = page.get("canonical") or page.get("title")
        names.extend(str(n) for n in [name, *(page.get("synonyms") or [])] if n)
    seen: set[str] = set()
    return [n for n in names if not (n.lower() in seen or seen.add(n.lower()))]


def card_people(persons, known: list[str]) -> dict[str, str]:
    """Every name a card may use, lower-cased, mapped to the person it means.

    The person pages map each of their names to the canonical name.
    Every other name in `known` maps to itself.
    A member that no filing has named yet can then still be who a note is about.
    """
    people = {name.lower(): p.canonical for p in persons for name in p.all_known_names()}
    for name in known:
        people.setdefault(name.lower(), name)
    return people


def named_in(text: str, people: "dict[str, str]") -> list[str]:
    """The people that `text` names, in the order it names them.

    A name counts as a whole word that is written as a name.
    "Will" in "Will and Sam" is a person. "will" in "Sam will bake" is not.
    """
    found: list[tuple[int, str]] = []
    for name, canonical in people.items():
        for m in re.finditer(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE):
            if m.group(0)[0].isupper():
                found.append((m.start(), canonical))
    return [canonical for _, canonical in sorted(found)]


def declared_people() -> list[DeclaredPerson] | None:
    """The declared person files, or None until `stack up memory` has written them.

    The function logs a file that does not read with its path and leaves it out.
    """
    return find_people(
        (os.environ.get("MEMORY_VAULT_DIR"), os.environ.get("BRAIN_REPO_DIR")),
        os.environ.get("SHARED_BUCKET") or DEFAULT_SHARED_BUCKET,
        report=lambda problem: _say(f"person file skipped: {problem}"),
    )


def _page_frontmatter(path: Path) -> dict:
    """A page's frontmatter, or {} when it has none that parses (and says so)."""
    try:
        return _parse_frontmatter_new(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    except FrontmatterError as e:
        _say(f"{path}: frontmatter does not parse ({e}), skipped")
        return {}


def known_names() -> list[str]:
    """The names of the people: the person files first, then the wiki's person pages.

    The person files declare each person with every name they go by, also a person without an account.
    Until the files exist, the account names stand in (FAMILY_NAMES from users.toml).
    The person pages add the spellings that documents used.
    The function reads what exists. Without a brain directory it returns the declared names alone.
    """
    brain = Path(os.environ.get("BRAIN_REPO_DIR", ""))
    abouts = sorted(brain.glob("*/about.md")) if brain.is_dir() else []
    declared = declared_people()
    if declared is None:
        return merge_names(os.environ.get("FAMILY_NAMES", ""), [_page_frontmatter(a) for a in abouts])
    # A person's page is the folder named by their id (`<id>/about.md`).
    ids = {p.id for p in declared}
    names = ",".join(n for p in declared for n in p.all_names())
    return merge_names(names, [_page_frontmatter(a) for a in abouts if a.parent.name in ids])


def declared_as_persons(declared: list[DeclaredPerson], learned: list[PersonPage]) -> list[PersonPage]:
    """The person files as the classifier's persons, with the first name as canonical name.

    Cards carry the first name, and the archivist files the `Person:` tag under it.
    A card about "Jo Roe" then lists Jo, as a document about Jo is tagged.
    The full name, the aliases and the spellings that documents used are the synonyms.
    """
    by_slug = {p.slug: p for p in learned}
    persons = []
    for d in declared:
        names = [n for n in d.all_names() if n != d.first_name]
        seen = by_slug.get(d.id)
        for n in seen.all_known_names() if seen else []:
            if n.lower() not in {x.lower() for x in [d.first_name, *names]}:
                names.append(n)
        persons.append(PersonPage(canonical=d.first_name, slug=d.id, synonyms=names))
    return persons
