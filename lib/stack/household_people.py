"""The household's people, read from the memory's files.

The classifier, the diary and whisper name the same people. They are
declared in the person files (`stack.people`) and described by the wiki's
person pages (`<vault>/<member>/about.md`):

    load_persons_from_vault(vault)         # [PersonPage("Homer", "homer", [...])]
    household_people()                     # ["Homer", "Marge", "Maggie"], from the person files
    card_people(persons, household)        # {"margaret": "Maggie", "homer": "Homer", ...}

Lives in the framework because the readers span stacklets: the archivist
and the diary read the same people, and a stacklet imports no other
stacklet. File reads and environment variables, stdlib only. The memory
stacklet re-exports these names. The household's topics are read by
`stack.household_topics`.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .frontmatter import FrontmatterError, card_fields, parse as _parse_frontmatter_new
from .people import Person as DeclaredPerson
from .people import find_people
from .vault import DEFAULT_SHARED_BUCKET

# ─── People ──────────────────────────────────────────────────────────────

# Skip set for `load_persons_from_vault`: top-level directories that
# carry an `about.md` but are not a household member. Kept in sync
# with the `stack memory wiki` command's own skip set; both walk the
# same vault structure.
_NON_MEMBER_DIRS = {".git", ".obsidian", "wiki", "private", "templates", "_shared"}


@dataclass
class PersonPage:
    """A person's wiki page (`<vault>/<id>/about.md`): the names it lists
    for one member. The person the family declares is `stack.people.Person`."""

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

    Each entity bucket directly under the vault root is treated as a
    potential household member. Buckets that are reserved for shared
    content (the `shared_bucket`, `wiki/`, `.git/`, ...) are skipped
    by name. A bucket without an `about.md` is also skipped -- a
    member can exist on the file system (with captured notes) before
    the wiki overview pass has generated their page.

    Pages tagged with a `kind:` other than `person` are skipped --
    leaves room for non-member entries to coexist under the same
    directory shape without leaking into the classifier prompt.
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


# ─── The household's names ───────────────────────────────────────────────

def _say(message: str) -> None:
    """Report on stderr: a command's log, which a reader of files must not fail on."""
    print(message, file=sys.stderr)


def persons_prompt_section(persons: List[PersonPage]) -> str:
    """Render the persons block embedded in the classifier prompt.

    Output shape, mirroring `correspondents_prompt_section`:

        Family members (canonical first name; synonyms in parens):
          - Marge (Marjorie, Margaret, Marge Bouvier, Marge Simpson)
          - Homer
          - Bart (Bartholomew, Bart Simpson)

    Returns an empty string when no curated person pages exist yet --
    the prompt builder falls back to the flat list of first names
    pulled from Paperless Person tags in that case.
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


def named_in(text: str, people: "dict[str, str]") -> list[str]:
    """The household members `text` names, in the order it names them.

    A name counts as a whole word written as a name: "Will" in "Will and
    Marge" is a person, "will" in "Marge will bake" is not.
    """
    found: list[tuple[int, str]] = []
    for name, canonical in people.items():
        for m in re.finditer(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE):
            if m.group(0)[0].isupper():
                found.append((m.start(), canonical))
    return [canonical for _, canonical in sorted(found)]


def declared_people() -> list[DeclaredPerson] | None:
    """The family's person files; None until `stack up memory` wrote them.

    A file that does not read is logged with its path and left out.
    """
    return find_people(
        (os.environ.get("MEMORY_VAULT_DIR"), os.environ.get("BRAIN_REPO_DIR")),
        os.environ.get("SHARED_BUCKET") or "family",
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
    declared = declared_people()
    if declared is None:
        return household(os.environ.get("FAMILY_NAMES", ""), [_page_frontmatter(a) for a in abouts])
    # A person's page is the folder named by their id (`<id>/about.md`).
    ids = {p.id for p in declared}
    names = ",".join(n for p in declared for n in p.all_names())
    return household(names, [_page_frontmatter(a) for a in abouts if a.parent.name in ids])


def declared_as_persons(declared: list[DeclaredPerson], learned: list[PersonPage]) -> list[PersonPage]:
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
        persons.append(PersonPage(canonical=d.first_name, slug=d.id, synonyms=names))
    return persons
